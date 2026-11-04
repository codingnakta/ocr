"""Build export files from the database."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import session_scope
from app.models import Document, Export, Page, utcnow
from app.pdf.exporter import ExportPage, ExportRegion, build_json, build_md, build_pdf, build_txt
from app.services.storage import doc_prefix, get_storage

MIME = {"pdf": "application/pdf", "txt": "text/plain; charset=utf-8", "md": "text/markdown; charset=utf-8",
        "json": "application/json"}


def collect_pages(db: Session, doc: Document, need_bytes: bool) -> list[ExportPage]:
    storage = get_storage()
    pages = list(db.scalars(select(Page).where(Page.document_id == doc.id).order_by(Page.position)))
    src_cache: dict[str, bytes] = {}
    out = []
    for n, p in enumerate(pages, 1):
        data = b""
        proc = None
        if need_bytes:
            key = p.source.storage_key
            if key not in src_cache:
                src_cache[key] = storage.get_bytes(key)
            data = src_cache[key]
            if doc.pdf_image_source == "enhanced" and p.processed_key and storage.exists(p.processed_key):
                proc = storage.get_bytes(p.processed_key)
        dw, dh = p.display_size
        regions = []
        for r in p.regions:
            regions.append(ExportRegion(
                text=r.text, quad_norm=r.polygon, block_index=r.block_index, block_type=r.block_type,
                uncertain=r.uncertain,
                meta={"id": r.id, "text_ocr": r.text_ocr, "text_claude": r.text_claude, "text_local": r.text_local,
                      "text_user": r.text_user, "engine": r.engine, "reviewed": r.reviewed, "flags": r.flags,
                      "position_source": r.position_source},
            ))
        out.append(ExportPage(
            number=n, kind=p.source.kind, source_bytes=data, mime=p.source.mime, pdf_index=p.source_page_index,
            user_rotation=p.user_rotation, display_w=dw, display_h=dh, content_type=p.content_type,
            regions=regions, processed_bytes=proc, processed_H=(p.preprocess_meta or {}).get("H"), label=p.label,
        ))
    return out


def run_export(export_id: str) -> None:
    storage = get_storage()
    with session_scope() as db:
        ex = db.get(Export, export_id)
        if ex is None:
            return
        doc = db.get(Document, ex.document_id)
        if doc is None:
            ex.status = "failed"
            ex.error = "문서가 삭제되었습니다."
            return
        fmt = ex.format
        pages = collect_pages(db, doc, need_bytes=(fmt == "pdf"))
        title = doc.title
        meta = {"document_id": doc.id, "title": doc.title, "exported_at": utcnow().isoformat(),
                "page_count": len(pages)}
        size_mode = (ex.options or {}).get("size_mode") or doc.page_size_mode
        image_source = (ex.options or {}).get("image_source") or doc.pdf_image_source
    report: dict = {}
    if fmt == "pdf":
        data, report = build_pdf(pages, size_mode=size_mode, image_source=image_source, title=title)
    elif fmt == "txt":
        data = build_txt(pages).encode("utf-8")
    elif fmt == "md":
        data = build_md(pages, title).encode("utf-8")
    elif fmt == "json":
        data = build_json(pages, meta).encode("utf-8")
    else:
        raise ValueError(f"unknown format {fmt}")
    key = f"{doc_prefix(meta['document_id'])}exports/{export_id}.{fmt}"
    storage.put_bytes(key, data, MIME[fmt])
    unaligned = sum(1 for p in pages for r in p.regions if r.quad_norm is None and r.text.strip())
    if report:
        report["unaligned_lines_excluded"] = unaligned
        # drop per-item detail to keep the stored report small
        for pg in report.get("pages", []):
            pg.pop("items", None)
    with session_scope() as db:
        ex = db.get(Export, export_id)
        if ex is None:
            storage.delete(key)
            return
        ex.status = "done"
        ex.storage_key = key
        ex.size_bytes = len(data)
        ex.report = report
        ex.finished_at = utcnow()
