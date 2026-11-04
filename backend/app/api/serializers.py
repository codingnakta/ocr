from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Document, Export, OcrRegion, Page


def region_out(r: OcrRegion) -> dict:
    alts = [f[4:] for f in (r.flags or []) if f.startswith("alt:")]
    unc_parts = []
    for f in r.flags or []:
        if f.startswith("uncertain_parts:"):
            unc_parts.extend(x for x in f[len("uncertain_parts:"):].split("|") if x)
    return {
        "id": r.id, "key": r.region_key, "order": r.order_index, "block": r.block_index, "block_type": r.block_type,
        "polygon": r.polygon, "position_source": r.position_source,
        "text": r.text, "text_ocr": r.text_ocr, "text_claude": r.text_claude, "text_local": r.text_local,
        "text_user": r.text_user, "engine": r.engine, "uncertain": r.uncertain, "reviewed": r.reviewed,
        "edited": r.text_user is not None, "aligned": r.polygon is not None,
        "flags": [f for f in (r.flags or []) if not f.startswith(("alt:", "uncertain_parts:"))],
        "alternatives": alts, "uncertain_parts": unc_parts, "local_score": r.local_score,
    }


def page_out(p: Page, number: int, doc_id: str) -> dict:
    w, h = p.display_size
    regs = p.regions
    return {
        "id": p.id, "number": number, "position": p.position, "label": p.label,
        "width": w, "height": h, "rotation": p.user_rotation, "content_type": p.content_type,
        "source_kind": p.source.kind if p.source else None,
        "existing_text_chars": p.existing_text_chars,
        "ocr_status": p.ocr_status, "ocr_engine": p.ocr_engine, "ocr_model": p.ocr_model, "ocr_error": p.ocr_error,
        "warnings": p.ocr_warnings or [],
        "regions": len(regs), "uncertain": sum(1 for r in regs if r.uncertain and not r.reviewed),
        "edited": sum(1 for r in regs if r.text_user is not None),
        "unaligned": sum(1 for r in regs if r.polygon is None and r.text.strip()),
        "thumb_url": f"/api/documents/{doc_id}/pages/{p.id}/thumbnail?v={p.user_rotation}",
        "image_url": f"/api/documents/{doc_id}/pages/{p.id}/image?v={p.user_rotation}",
        "processed_url": f"/api/documents/{doc_id}/pages/{p.id}/image?variant=processed" if p.processed_key else None,
    }


def document_out(db: Session, doc: Document, with_pages: bool = True) -> dict:
    from app.services.jobs import active_job, progress

    pages = list(doc.pages)
    out = {
        "id": doc.id, "title": doc.title, "page_size_mode": doc.page_size_mode,
        "pdf_image_source": doc.pdf_image_source,
        "created_at": doc.created_at.isoformat(), "updated_at": doc.updated_at.isoformat(),
        "page_count": len(pages),
        "ocr_done": sum(1 for p in pages if p.ocr_status == "done"),
        "ocr_failed": sum(1 for p in pages if p.ocr_status == "failed"),
        "files": [{"id": f.id, "name": f.filename, "kind": f.kind, "size": f.size_bytes, "pages": f.page_count}
                  for f in doc.files],
    }
    if with_pages:
        out["pages"] = [page_out(p, i + 1, doc.id) for i, p in enumerate(pages)]
        from app.models import Job

        job = active_job(db, doc.id) or db.scalar(
            select(Job).where(Job.document_id == doc.id, Job.kind == "ocr").order_by(Job.created_at.desc()))
        # latest OCR job (active or finished) so the UI can show "completed" / failures
        out["active_job"] = progress(db, job) if job else None
        exps = db.scalars(select(Export).where(Export.document_id == doc.id).order_by(Export.created_at.desc())
                          .limit(20)).all()
        out["exports"] = [export_out(e) for e in exps]
    else:
        out["thumb_url"] = pages[0] and f"/api/documents/{doc.id}/pages/{pages[0].id}/thumbnail" if pages else None
    return out


def export_out(e: Export) -> dict:
    return {
        "id": e.id, "format": e.format, "status": e.status, "size_bytes": e.size_bytes, "error": e.error,
        "report": e.report or {}, "options": e.options or {},
        "created_at": e.created_at.isoformat(), "finished_at": e.finished_at.isoformat() if e.finished_at else None,
        "download_url": f"/api/exports/{e.id}/download" if e.status == "done" else None,
    }


def count_pages(db: Session, doc_id: str) -> int:
    return db.scalar(select(func.count()).select_from(Page).where(Page.document_id == doc_id)) or 0
