"""OCR job lifecycle: create, pause, resume, cancel, retry, progress."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ApiCall, Document, Job, Page, PageTask, utcnow
from app.services import cost as cost_svc

ACTIVE = ("queued", "running", "paused")


class JobError(ValueError):
    pass


def active_job(db: Session, document_id: str) -> Job | None:
    return db.scalar(select(Job).where(Job.document_id == document_id, Job.kind == "ocr",
                                       Job.status.in_(ACTIVE)).order_by(Job.created_at.desc()))


def pages_for_job(db: Session, doc: Document, page_ids: list[str] | None, rerun: bool) -> list[Page]:
    pages = list(db.scalars(select(Page).where(Page.document_id == doc.id).order_by(Page.position)))
    if page_ids:
        wanted = set(page_ids)
        pages = [p for p in pages if p.id in wanted]
        return [p for p in pages if p.content_type not in ("text", "empty") or rerun]
    out = []
    for p in pages:
        if p.content_type in ("text", "empty") and not rerun:
            continue
        if p.ocr_status == "done" and not rerun:
            continue
        out.append(p)
    return out


def estimate(db: Session, doc: Document, mode: str, page_ids: list[str] | None, rerun: bool,
             model: str | None = None) -> dict:
    s = get_settings()
    model = model or s.claude_model
    pages = pages_for_job(db, doc, page_ids, rerun)
    if mode == "local":
        return {"pages": len(pages), "model": None, "estimated_cost_usd": 0.0, "is_estimate": True,
                "per_page_usd": 0.0, "api_calls": 0, "basis": "로컬 OCR은 API 비용이 없습니다."}
    total = 0.0
    total_in = total_out = 0
    for p in pages:
        w, h = p.display_size
        e = cost_svc.estimate_page(w, h, regions=25, model=model, reread_fraction=0.15 if mode == "hybrid" else 0.0)
        total += e.cost_usd
        total_in += e.input_tokens
        total_out += e.output_tokens
    ratio = cost_svc.learned_ratio(db, model)
    basis = "페이지당 약 25줄 가정, 이미지 토큰은 공식 계산식(⌈w/28⌉×⌈h/28⌉) 기준"
    if ratio:
        total *= ratio
        basis += f", 최근 실제 호출 비용으로 보정(×{ratio:.2f})"
    price, known = cost_svc.price_for(model)
    return {
        "pages": len(pages), "model": model, "estimated_cost_usd": round(total, 4), "is_estimate": True,
        "per_page_usd": round(total / len(pages), 4) if pages else 0.0,
        "input_tokens": total_in, "output_tokens": total_out,
        "api_calls": len(pages) * (1 if mode == "ai" else 1.15),
        "pricing_known": known, "pricing_checked": cost_svc.PRICING_CHECKED, "basis": basis,
        "price_per_mtok": {"input": price.input, "output": price.output} if price else None,
    }


def create_ocr_job(db: Session, doc: Document, owner_id: str, mode: str, page_ids: list[str] | None,
                   rerun: bool, cost_limit: float | None, params: dict) -> Job:
    s = get_settings()
    if mode not in ("ai", "local", "hybrid"):
        raise JobError("mode는 ai, local, hybrid 중 하나여야 합니다.")
    if mode != "local" and not s.claude_configured:
        raise JobError("Claude API 키가 설정되지 않아 AI/하이브리드 모드를 사용할 수 없습니다.")
    if active_job(db, doc.id):
        raise JobError("이 문서에 이미 진행 중인 OCR 작업이 있습니다.")
    pages = pages_for_job(db, doc, page_ids, rerun)
    if not pages:
        raise JobError("OCR할 페이지가 없습니다 (이미 완료되었거나 텍스트가 있는 PDF 페이지).")
    limit = cost_limit if cost_limit is not None else s.default_job_cost_limit_usd
    if mode == "local":
        limit = None
    job = Job(document_id=doc.id, owner_id=owner_id, kind="ocr", status="queued", mode=mode,
              params={**params, "rerun": rerun}, cost_limit_usd=limit)
    db.add(job)
    db.flush()
    for p in pages:
        db.add(PageTask(job_id=job.id, page_id=p.id, seq=p.position, status="pending"))
    return job


def pause(db: Session, job: Job) -> Job:
    if job.status not in ("queued", "running"):
        raise JobError(f"'{job.status}' 상태의 작업은 일시 중지할 수 없습니다.")
    job.status = "paused"
    job.stop_reason = "user_pause"
    return job


def resume(db: Session, job: Job, new_cost_limit: float | None = None) -> Job:
    if job.status not in ("paused", "completed_with_errors", "failed"):
        raise JobError(f"'{job.status}' 상태의 작업은 재개할 수 없습니다.")
    if new_cost_limit is not None:
        job.cost_limit_usd = new_cost_limit
    pending = db.scalar(select(func.count()).select_from(PageTask).where(
        PageTask.job_id == job.id, PageTask.status.in_(("pending", "running", "failed"))))
    if not pending:
        raise JobError("재개할 페이지가 없습니다.")
    db.execute(update(PageTask).where(PageTask.job_id == job.id, PageTask.status == "failed")
               .values(status="pending", last_error=None, attempts=0))
    job.status = "queued"
    job.stop_reason = None
    job.error = None
    job.finished_at = None
    return job


def retry_failed(db: Session, job: Job) -> int:
    if job.status in ("queued", "running"):
        raise JobError("작업이 진행 중입니다.")
    res = db.execute(update(PageTask).where(PageTask.job_id == job.id, PageTask.status == "failed")
                     .values(status="pending", last_error=None, attempts=0))
    n = res.rowcount or 0
    if n:
        job.status = "queued"
        job.stop_reason = None
        job.finished_at = None
    return n


def cancel(db: Session, job: Job) -> Job:
    if job.status in ("completed", "cancelled"):
        raise JobError("이미 종료된 작업입니다.")
    job.status = "cancelled"
    job.stop_reason = "user_cancel"
    job.finished_at = utcnow()
    db.execute(update(PageTask).where(PageTask.job_id == job.id, PageTask.status == "pending")
               .values(status="cancelled"))
    return job


def finalize_if_done(db: Session, job: Job) -> None:
    counts = dict(db.execute(select(PageTask.status, func.count()).where(PageTask.job_id == job.id)
                             .group_by(PageTask.status)).all())
    if counts.get("pending") or counts.get("running"):
        return
    if job.status in ("cancelled", "paused"):
        return
    job.status = "completed_with_errors" if counts.get("failed") else "completed"
    job.finished_at = utcnow()


def progress(db: Session, job: Job) -> dict:
    counts = dict(db.execute(select(PageTask.status, func.count()).where(PageTask.job_id == job.id)
                             .group_by(PageTask.status)).all())
    total = sum(counts.values())
    done = counts.get("done", 0) + counts.get("skipped", 0)
    running = list(db.execute(
        select(PageTask.page_id, Page.position).join(Page, Page.id == PageTask.page_id)
        .where(PageTask.job_id == job.id, PageTask.status == "running").order_by(Page.position)).all())
    agg = db.execute(select(
        func.count(ApiCall.id), func.coalesce(func.sum(ApiCall.input_tokens), 0),
        func.coalesce(func.sum(ApiCall.output_tokens), 0), func.coalesce(func.sum(ApiCall.thinking_tokens), 0),
        func.coalesce(func.sum(ApiCall.cost_usd), 0.0),
    ).where(ApiCall.job_id == job.id)).one()
    by_status = dict(db.execute(select(ApiCall.status, func.count()).where(ApiCall.job_id == job.id)
                                .group_by(ApiCall.status)).all())
    retries = db.scalar(select(func.count()).select_from(ApiCall).where(ApiCall.job_id == job.id,
                                                                         ApiCall.sdk_retries > 0)) or 0
    unc_est = db.scalar(select(func.coalesce(func.sum(ApiCall.estimated_cost_usd), 0.0)).where(
        ApiCall.job_id == job.id, ApiCall.status == "uncertain")) or 0.0
    failed_pages = list(db.execute(
        select(Page.id, Page.position, PageTask.last_error).join(Page, Page.id == PageTask.page_id)
        .where(PageTask.job_id == job.id, PageTask.status == "failed").order_by(Page.position)).all())
    cached = db.scalar(select(func.count()).select_from(PageTask).where(PageTask.job_id == job.id,
                                                                         PageTask.from_cache.is_(True))) or 0
    cost_actual = float(agg[4])
    return {
        "id": job.id, "document_id": job.document_id, "status": job.status, "mode": job.mode,
        "stop_reason": job.stop_reason, "error": job.error,
        "total_pages": total, "completed_pages": done, "failed_pages": counts.get("failed", 0),
        "pending_pages": counts.get("pending", 0), "running_pages": counts.get("running", 0),
        "cancelled_pages": counts.get("cancelled", 0), "from_cache_pages": cached,
        "current_pages": [pos + 1 for _, pos in running],
        "progress": round(done / total, 4) if total else 0.0,
        "remaining_pages": counts.get("pending", 0) + counts.get("running", 0),
        "api": {
            "calls": int(agg[0]), "succeeded": by_status.get("succeeded", 0), "failed": by_status.get("failed", 0),
            "uncertain": by_status.get("uncertain", 0), "in_flight": by_status.get("started", 0),
            "retries": int(retries), "input_tokens": int(agg[1]), "output_tokens": int(agg[2]),
            "thinking_tokens": int(agg[3]), "cost_usd": round(cost_actual, 6),
            "uncertain_cost_estimate_usd": round(float(unc_est), 6),
            "model": (job.params or {}).get("model") or get_settings().claude_model if job.mode != "local" else None,
        },
        "cost_limit_usd": job.cost_limit_usd,
        "failed": [{"page_id": pid, "page_number": pos + 1, "error": err} for pid, pos, err in failed_pages[:50]],
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
