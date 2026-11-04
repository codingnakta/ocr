"""OCR jobs (start, status, pause/resume/cancel/retry) and exports."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import owned_document, owned_export, owned_job
from app.api.serializers import export_out
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import current_user, limiter
from app.models import Document, Export, Job, User
from app.schemas.api import EstimateRequest, ExportRequest, JobResume, OcrRequest
from app.services import jobs as job_svc
from app.services.export_service import MIME
from app.services.storage import get_storage

router = APIRouter(prefix="/api", tags=["jobs"])


@router.post("/documents/{document_id}/ocr/estimate")
def estimate(body: EstimateRequest, doc: Document = Depends(owned_document), db: Session = Depends(get_db)):
    return job_svc.estimate(db, doc, body.mode, body.page_ids, body.rerun)


@router.post("/documents/{document_id}/ocr", status_code=202)
def start_ocr(body: OcrRequest, doc: Document = Depends(owned_document), db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    limiter.hit(f"ocr:{user.id}", get_settings().rate_limit_ocr_starts_per_hour, 3600)
    params = {"effort": body.effort, "preprocess": body.preprocess or {}, "use_cache": body.use_cache,
              "model": get_settings().claude_model}
    try:
        job = job_svc.create_ocr_job(db, doc, user.id, body.mode, body.page_ids, body.rerun,
                                     body.cost_limit_usd, params)
    except job_svc.JobError as e:
        db.rollback()
        raise HTTPException(409 if "진행 중" in str(e) else 400, str(e)) from e
    db.commit()
    return job_svc.progress(db, job)


@router.get("/documents/{document_id}/jobs")
def list_jobs(doc: Document = Depends(owned_document), db: Session = Depends(get_db)):
    jobs = db.scalars(select(Job).where(Job.document_id == doc.id).order_by(Job.created_at.desc()).limit(30)).all()
    return {"jobs": [job_svc.progress(db, j) for j in jobs]}


@router.get("/jobs/{job_id}")
def get_job(job: Job = Depends(owned_job), db: Session = Depends(get_db)):
    return job_svc.progress(db, job)


def _transition(fn, job: Job, db: Session, *args):
    try:
        fn(db, job, *args)
    except job_svc.JobError as e:
        db.rollback()
        raise HTTPException(409, str(e)) from e
    db.commit()
    return job_svc.progress(db, job)


@router.post("/jobs/{job_id}/pause")
def pause_job(job: Job = Depends(owned_job), db: Session = Depends(get_db)):
    return _transition(job_svc.pause, job, db)


@router.post("/jobs/{job_id}/resume")
def resume_job(body: JobResume | None = None, job: Job = Depends(owned_job), db: Session = Depends(get_db)):
    return _transition(job_svc.resume, job, db, body.cost_limit_usd if body else None)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job: Job = Depends(owned_job), db: Session = Depends(get_db)):
    return _transition(job_svc.cancel, job, db)


@router.post("/jobs/{job_id}/retry-failed")
def retry_failed(job: Job = Depends(owned_job), db: Session = Depends(get_db)):
    try:
        n = job_svc.retry_failed(db, job)
    except job_svc.JobError as e:
        raise HTTPException(409, str(e)) from e
    if not n:
        raise HTTPException(409, "실패한 페이지가 없습니다.")
    db.commit()
    return job_svc.progress(db, job)


# ---------------------------------------------------------------- export --
@router.post("/documents/{document_id}/export", status_code=202)
def start_export(body: ExportRequest, doc: Document = Depends(owned_document), db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    if not doc.pages:
        raise HTTPException(400, "페이지가 없습니다.")
    limiter.hit(f"export:{user.id}", 60, 60)
    ex = Export(document_id=doc.id, owner_id=user.id, format=body.format, status="queued",
                options={"size_mode": body.size_mode, "image_source": body.image_source})
    db.add(ex)
    db.commit()
    return export_out(ex)


@router.get("/exports/{export_id}")
def get_export(ex: Export = Depends(owned_export)):
    return export_out(ex)


def _stream(ex: Export, doc_title: str) -> StreamingResponse:
    from urllib.parse import quote

    storage = get_storage()
    if ex.status != "done" or not ex.storage_key or not storage.exists(ex.storage_key):
        raise HTTPException(404, "다운로드할 파일이 없습니다.")
    safe = "".join(ch for ch in doc_title if ch.isalnum() or ch in " _-()").strip() or "document"
    fname = f"{safe}.{ex.format}"
    headers = {
        "Content-Disposition": f"attachment; filename=\"document.{ex.format}\"; filename*=UTF-8''{quote(fname)}",
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if ex.size_bytes:
        headers["Content-Length"] = str(ex.size_bytes)
    return StreamingResponse(storage.iter_chunks(ex.storage_key), media_type=MIME[ex.format], headers=headers)


@router.get("/exports/{export_id}/download")
def download_export(ex: Export = Depends(owned_export), db: Session = Depends(get_db)):
    doc = db.get(Document, ex.document_id)
    return _stream(ex, doc.title if doc else "document")


@router.get("/documents/{document_id}/download")
def download_latest(format: str = Query("pdf", pattern="^(pdf|txt|md|json)$"),
                    doc: Document = Depends(owned_document), db: Session = Depends(get_db)):
    ex = db.scalar(select(Export).where(Export.document_id == doc.id, Export.format == format,
                                        Export.status == "done").order_by(Export.created_at.desc()))
    if ex is None:
        raise HTTPException(404, "먼저 내보내기를 실행하세요.")
    return _stream(ex, doc.title)
