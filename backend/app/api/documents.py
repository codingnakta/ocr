"""Documents, files, pages, page images and OCR results."""

from __future__ import annotations

import logging

import numpy as np
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import owned_document, resolve_page
from app.api.serializers import document_out, page_out, region_out
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import current_user, limiter
from app.imaging.geometry import rotate_norm_quad, rotation_h
from app.imaging.io import encode_jpeg, page_display_image, pil_to_bgr
from app.models import Document, OcrRegion, Page, User, utcnow
from app.ocr.base import CostLimitExceeded, OcrError
from app.schemas.api import DocumentCreate, DocumentPatch, OcrPatch, PageOrder, PagePatch, RegionReocr
from app.services.ingest import UploadError, ingest_file
from app.services.retention import delete_document_files
from app.services.storage import doc_prefix, get_storage

log = logging.getLogger("pilgibot.documents")

router = APIRouter(prefix="/api/documents", tags=["documents"])


async def _read_limited(f: UploadFile, limit: int) -> bytes:
    chunks, size = [], 0
    while True:
        chunk = await f.read(1 << 20)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise UploadError(f"파일이 너무 큽니다 (최대 {limit // (1024 * 1024)}MB).")
        chunks.append(chunk)
    return b"".join(chunks)


async def _ingest_uploads(db: Session, doc: Document, files: list[UploadFile]) -> dict:
    s = get_settings()
    added, errors = [], []
    added_ids: list[str] = []
    for f in files:
        try:
            data = await _read_limited(f, s.max_file_bytes)
            pages = ingest_file(db, doc, f.filename, data, f.content_type)
            added_ids.extend(p.id for p in pages)
            db.commit()
            added.extend(pages)
        except UploadError as e:
            db.rollback()
            errors.append({"file": (f.filename or "")[:120], "error": str(e)})
        except Exception as e:
            db.rollback()
            log.exception("ingest failed for %r", f.filename)
            errors.append({"file": (f.filename or "")[:120],
                           "error": f"파일 처리 중 오류가 발생했습니다 ({type(e).__name__}: {str(e)[:120]})."})
        finally:
            await f.close()
    doc.updated_at = utcnow()
    db.commit()
    return {"added_pages": len(added), "page_ids": added_ids, "errors": errors}


@router.get("")
def list_documents(db: Session = Depends(get_db), user: User = Depends(current_user)):
    docs = db.scalars(select(Document).where(Document.owner_id == user.id).order_by(Document.updated_at.desc())
                      .limit(500)).all()
    return {"documents": [document_out(db, d, with_pages=False) for d in docs]}


@router.post("", status_code=201)
async def create_document(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user),
                          files: list[UploadFile] | None = File(default=None), title: str | None = Form(default=None)):
    """Create a document. JSON body {title} or multipart with `files` (+ optional `title`)."""
    s = get_settings()
    n = db.scalar(select(func.count()).select_from(Document).where(Document.owner_id == user.id)) or 0
    if n >= s.max_documents_per_user:
        raise HTTPException(400, f"문서는 최대 {s.max_documents_per_user}개까지 만들 수 있습니다. 기존 문서를 삭제해 주세요.")
    if request.headers.get("content-type", "").startswith("application/json"):
        body = DocumentCreate.model_validate(await request.json())
        title = body.title
    doc = Document(owner_id=user.id, title=(title or "").strip()[:200] or f"새 문서 {utcnow():%Y-%m-%d %H:%M}")
    db.add(doc)
    db.commit()
    result = {"added_pages": 0, "errors": []}
    if files:
        limiter.hit(f"upload:{user.id}", s.rate_limit_uploads_per_min, 60)
        result = await _ingest_uploads(db, doc, files)
    db.refresh(doc)
    return {**document_out(db, doc), "upload": result}


@router.post("/{document_id}/files")
async def upload_files(files: list[UploadFile] = File(...), brief: bool = Query(False),
                       doc: Document = Depends(owned_document),
                       db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Add files. `brief=1` returns only the new page ids (used for big batch uploads)."""
    limiter.hit(f"upload:{user.id}", get_settings().rate_limit_uploads_per_min, 60)
    from app.services.jobs import active_job

    j = active_job(db, doc.id)
    result = await _ingest_uploads(db, doc, files)
    if brief:
        return {"upload": result, "page_count": len(doc.pages)}
    db.refresh(doc)
    out = document_out(db, doc)
    out["upload"] = result
    if j:
        out["upload"]["note"] = "진행 중인 OCR 작업에는 새 페이지가 포함되지 않습니다."
    return out


@router.get("/{document_id}")
def get_document(doc: Document = Depends(owned_document), db: Session = Depends(get_db)):
    return document_out(db, doc)


@router.patch("/{document_id}")
def patch_document(body: DocumentPatch, doc: Document = Depends(owned_document), db: Session = Depends(get_db)):
    if body.title is not None:
        doc.title = body.title.strip()[:200] or doc.title
    if body.page_size_mode:
        doc.page_size_mode = body.page_size_mode
    if body.pdf_image_source:
        doc.pdf_image_source = body.pdf_image_source
    db.commit()
    return document_out(db, doc)


@router.delete("/{document_id}", status_code=204)
def delete_document(doc: Document = Depends(owned_document), db: Session = Depends(get_db)):
    doc_id = doc.id
    db.delete(doc)
    db.commit()
    delete_document_files(doc_id)
    return Response(status_code=204)


# ------------------------------------------------------------------ pages --
@router.put("/{document_id}/pages/order")
def reorder_pages(body: PageOrder, doc: Document = Depends(owned_document), db: Session = Depends(get_db)):
    pages = {p.id: p for p in doc.pages}
    if set(body.page_ids) != set(pages) or len(body.page_ids) != len(pages):
        raise HTTPException(400, "페이지 목록이 문서와 일치하지 않습니다.")
    for i, pid in enumerate(body.page_ids):
        pages[pid].position = i
    doc.updated_at = utcnow()
    db.commit()
    db.expire(doc)
    return document_out(db, doc)


@router.patch("/{document_id}/pages/{page_ref}")
def patch_page(page_ref: str, body: PagePatch, doc: Document = Depends(owned_document), db: Session = Depends(get_db)):
    page = resolve_page(db, doc, page_ref)
    if body.rotate:
        delta = body.rotate % 360
        old_w, old_h = page.display_size
        page.user_rotation = (page.user_rotation + delta) % 360
        for r in page.regions:
            if r.polygon:
                r.polygon = rotate_norm_quad(r.polygon, delta)
        meta = dict(page.preprocess_meta or {})
        if meta.get("H"):
            R = rotation_h(delta, old_w, old_h)
            meta["H"] = (np.asarray(meta["H"]) @ np.linalg.inv(R)).tolist()
            meta["display_size"] = list(page.display_size)
            page.preprocess_meta = meta
    if body.label is not None:
        page.label = body.label[:255]
    doc.updated_at = utcnow()
    db.commit()
    pages = list(doc.pages)
    return page_out(page, pages.index(page) + 1, doc.id)


@router.delete("/{document_id}/pages/{page_ref}", status_code=204)
def delete_page(page_ref: str, doc: Document = Depends(owned_document), db: Session = Depends(get_db)):
    page = resolve_page(db, doc, page_ref)
    storage = get_storage()
    for key in (page.thumb_key, page.processed_key):
        if key:
            storage.delete(key)
    db.delete(page)
    db.flush()
    for i, p in enumerate(db.scalars(select(Page).where(Page.document_id == doc.id).order_by(Page.position))):
        p.position = i
    doc.updated_at = utcnow()
    db.commit()
    return Response(status_code=204)


@router.get("/{document_id}/pages/{page_ref}/thumbnail")
def page_thumbnail(page_ref: str, doc: Document = Depends(owned_document), db: Session = Depends(get_db)):
    page = resolve_page(db, doc, page_ref)
    storage = get_storage()
    if not page.thumb_key or not storage.exists(page.thumb_key):
        raise HTTPException(404, "썸네일이 없습니다.")
    data = storage.get_bytes(page.thumb_key)
    if page.user_rotation:
        from app.imaging.io import load_oriented, make_thumbnail, rotate_cw

        data = make_thumbnail(rotate_cw(load_oriented(data), page.user_rotation))
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{document_id}/pages/{page_ref}/image")
def page_image(page_ref: str, variant: str = Query("display", pattern="^(display|processed)$"),
               max_side: int = Query(2200, ge=200, le=5000), doc: Document = Depends(owned_document),
               db: Session = Depends(get_db)):
    page = resolve_page(db, doc, page_ref)
    storage = get_storage()
    if variant == "processed":
        if not page.processed_key or not storage.exists(page.processed_key):
            raise HTTPException(404, "전처리 이미지가 없습니다.")
        return Response(storage.get_bytes(page.processed_key), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=600"})
    key = f"{doc_prefix(doc.id)}preview/{page.id}_{page.user_rotation}_{max_side}.jpg"
    if storage.exists(key):
        data = storage.get_bytes(key)
    else:
        img = page_display_image(page, storage.get_bytes(page.source.storage_key), dpi=150)
        img.thumbnail((max_side, max_side))
        data = encode_jpeg(img, 88)
        storage.put_bytes(key, data, "image/jpeg")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


# -------------------------------------------------------------------- OCR --
@router.get("/{document_id}/pages/{page_ref}/ocr")
def get_page_ocr(page_ref: str, doc: Document = Depends(owned_document), db: Session = Depends(get_db)):
    page = resolve_page(db, doc, page_ref)
    pages = list(doc.pages)
    return {"page": page_out(page, pages.index(page) + 1, doc.id),
            "regions": [region_out(r) for r in page.regions],
            "preprocess": {k: v for k, v in (page.preprocess_meta or {}).items() if k != "H"}}


@router.patch("/{document_id}/pages/{page_ref}/ocr")
def patch_page_ocr(page_ref: str, body: OcrPatch, doc: Document = Depends(owned_document),
                   db: Session = Depends(get_db)):
    page = resolve_page(db, doc, page_ref)
    by_id = {r.id: r for r in page.regions}
    for e in body.regions:
        r = by_id.get(e.id)
        if r is None:
            raise HTTPException(404, f"영역 {e.id}를 찾을 수 없습니다.")
        if e.revert:
            r.text_user = None
        elif e.text_user is not None:
            # store the user's text; machine re-runs never overwrite it
            r.text_user = e.text_user
        if e.reviewed is not None:
            r.reviewed = e.reviewed
    for rid in body.delete:
        r = by_id.get(rid)
        if r is not None:
            db.delete(r)
    n = len(page.regions)
    for i, c in enumerate(body.create):
        poly = [[min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0)] for x, y in c.polygon]
        db.add(OcrRegion(page_id=page.id, region_key=f"m{n + i + 1}", order_index=n + i, polygon=poly,
                         position_source="user", text_ocr="", text_user=c.text_user, engine="user",
                         reviewed=True, flags=["user_created"]))
    if page.ocr_status in ("none", "failed") and (body.create or body.regions):
        page.ocr_status = "done"
        page.ocr_engine = page.ocr_engine or "user"
    doc.updated_at = utcnow()
    db.commit()
    db.refresh(page)
    return {"regions": [region_out(r) for r in page.regions]}


@router.post("/{document_id}/pages/{page_ref}/reocr")
def reocr_region(page_ref: str, body: RegionReocr, doc: Document = Depends(owned_document),
                 db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Re-recognize a user-selected area (or an existing region). Returns a candidate
    text; it is applied only when the user accepts it (PATCH .../ocr) or when
    `create_region` is set for a new area."""
    from app.imaging.geometry import crop_quad, denormalize_quad
    from app.ocr.local_engine import get_local_engine
    from app.services.ocr_service import DbLedger

    s = get_settings()
    limiter.hit(f"reocr:{user.id}", 60, 60)
    page = resolve_page(db, doc, page_ref)
    target = None
    if body.region_id:
        target = next((r for r in page.regions if r.id == body.region_id), None)
        if target is None or not target.polygon:
            raise HTTPException(404, "위치가 있는 영역을 찾을 수 없습니다.")
        quad = target.polygon
    elif body.rect:
        x0, y0, x1, y1 = [min(max(v, 0.0), 1.0) for v in body.rect]
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        if (x1 - x0) < 0.005 or (y1 - y0) < 0.003:
            raise HTTPException(400, "선택 영역이 너무 작습니다.")
        quad = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    else:
        raise HTTPException(400, "rect 또는 region_id가 필요합니다.")
    img = pil_to_bgr(page_display_image(page, get_storage().get_bytes(page.source.storage_key)))
    h, w = img.shape[:2]
    crop = crop_quad(img, denormalize_quad(quad, w, h), pad=0.15)
    try:
        if body.engine == "local":
            local = get_local_engine()
            if local is None:
                raise HTTPException(400, "로컬 OCR이 비활성화되어 있습니다.")
            lines = local.recognize_page(crop)
            text = "\n".join(ln.local_text or "" for ln in lines) if lines else local.recognize_crops([crop])[0][0]
            uncertain, model, engine = False, local.rec_model, "local"
        else:
            if not s.claude_configured:
                raise HTTPException(400, "Claude API 키가 설정되지 않았습니다.")
            from app.ocr.claude_engine import get_claude_engine
            from app.ocr.hybrid_engine import prepare_crop

            eng = get_claude_engine()
            ledger = DbLedger(user.id, None, page.id, None)
            pc = prepare_crop(img, denormalize_quad(quad, w, h)) if target is not None else crop
            out, usage = eng.reread_lines([("sel", pc)], ledger, purpose="region")
            line = next((x for x in out.lines if x.id == "sel"), out.lines[0] if out.lines else None)
            text = line.text if line else ""
            uncertain = bool(line and line.uncertain)
            model, engine = eng.model, "claude_region"
    except CostLimitExceeded as e:
        raise HTTPException(402, str(e)) from e
    except OcrError as e:
        raise HTTPException(502, str(e)) from e
    created = None
    if body.create_region and not target:
        n = len(page.regions)
        reg = OcrRegion(page_id=page.id, region_key=f"m{n + 1}", order_index=n, polygon=quad,
                        position_source="user", text_ocr=text, engine=engine, uncertain=uncertain,
                        flags=["user_area"])
        db.add(reg)
        if page.ocr_status in ("none", "failed"):
            page.ocr_status = "done"
        db.commit()
        db.refresh(reg)
        created = region_out(reg)
    return {"text": text, "uncertain": uncertain, "engine": engine, "model": model, "created": created,
            "note": "후보 결과입니다. '적용'을 눌러야 해당 영역에 반영됩니다." if not created else None}
