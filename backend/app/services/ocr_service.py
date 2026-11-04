"""Per-page OCR processing with ledger, cost limits, caching and edit preservation."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

import cv2
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import session_scope
from app.imaging.geometry import iou_boxes, quad_bbox
from app.imaging.io import page_display_image, pil_to_bgr
from app.models import ApiCall, Job, OcrCache, OcrRegion, OcrRun, Page, utcnow
from app.ocr.base import CostLimitExceeded, PageOcrResult
from app.ocr.local_engine import get_local_engine
from app.ocr.pipeline import PIPELINE_VERSION
from app.ocr.prompts import PROMPT_VERSION
from app.services.storage import doc_prefix, get_storage

log = logging.getLogger(__name__)


# ------------------------------------------------------------- spending --
def spent_usd(db: Session, *, job_id: str | None = None, owner_id: str | None = None,
              since: datetime | None = None) -> float:
    """Actual cost of finished calls + *estimated* cost of uncertain/in-flight calls
    (they may have been billed, so they count against limits)."""
    q_actual = select(func.coalesce(func.sum(ApiCall.cost_usd), 0.0)).where(ApiCall.status.in_(("succeeded", "failed")))
    q_unc = select(func.coalesce(func.sum(ApiCall.estimated_cost_usd), 0.0)).where(
        ApiCall.status.in_(("uncertain", "started")))
    for cond in ([ApiCall.job_id == job_id] if job_id else []) + ([ApiCall.owner_id == owner_id] if owner_id else []) + \
            ([ApiCall.started_at >= since] if since else []):
        q_actual = q_actual.where(cond)
        q_unc = q_unc.where(cond)
    return float(db.scalar(q_actual) or 0.0) + float(db.scalar(q_unc) or 0.0)


def today_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


class DbLedger:
    """Writes every Claude attempt to `api_calls` in its own short transaction, so the
    record survives even if the page processing later fails or the process dies."""

    def __init__(self, owner_id: str | None, job_id: str | None, page_id: str | None,
                 job_limit: float | None = None):
        self.owner_id, self.job_id, self.page_id = owner_id, job_id, page_id
        self.job_limit = job_limit
        self.calls = 0
        self.cost = 0.0

    def check_budget(self, estimated_cost: float) -> None:
        s = get_settings()
        with session_scope() as db:
            if self.job_id and self.job_limit is not None:
                spent = spent_usd(db, job_id=self.job_id)
                if spent + estimated_cost > self.job_limit:
                    raise CostLimitExceeded(
                        f"작업 비용 상한 ${self.job_limit:.2f}에 도달합니다 (사용 ${spent:.3f} + 예상 ${estimated_cost:.3f}).")
            if self.owner_id and s.daily_cost_limit_usd > 0:
                spent_d = spent_usd(db, owner_id=self.owner_id, since=today_start())
                if spent_d + estimated_cost > s.daily_cost_limit_usd:
                    raise CostLimitExceeded(f"일일 비용 상한 ${s.daily_cost_limit_usd:.2f}에 도달합니다.")

    def start(self, purpose, model, request_hash, estimated_cost, attempt) -> str:
        with session_scope() as db:
            c = ApiCall(owner_id=self.owner_id, job_id=self.job_id, page_id=self.page_id, purpose=purpose,
                        model=model, status="started", request_hash=request_hash,
                        estimated_cost_usd=estimated_cost, sdk_retries=attempt - 1)
            db.add(c)
            db.flush()
            return c.id

    def finish(self, call_id: str, **fields) -> None:
        allowed = {"status", "input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens",
                   "cache_write_tokens", "cost_usd", "served_model", "stop_reason", "request_id", "error"}
        vals = {k: v for k, v in fields.items() if k in allowed}
        vals["finished_at"] = utcnow()
        if fields.get("cost_is_estimate"):
            vals["error"] = (vals.get("error") or "") + " [cost estimated: model not in pricing table or fallback]"
        with session_scope() as db:
            db.execute(update(ApiCall).where(ApiCall.id == call_id).values(**vals))
        self.calls += 1
        self.cost += float(fields.get("cost_usd") or 0.0)


def mark_stale_calls_uncertain(max_age_s: int = 600) -> int:
    """Calls left in `started` (process died mid-request) become `uncertain`."""
    cutoff = utcnow() - timedelta(seconds=max_age_s)
    with session_scope() as db:
        res = db.execute(update(ApiCall).where(ApiCall.status == "started", ApiCall.started_at < cutoff)
                         .values(status="uncertain", error="중단됨: 요청 완료 여부 불확실 (중복 과금 가능)",
                                 finished_at=utcnow()))
        return res.rowcount or 0


# ---------------------------------------------------------------- cache --
def cache_key(page: Page, mode: str, steps: dict, model: str | None, effort: str | None) -> str:
    local = get_local_engine()
    parts = {
        "sha": page.source.sha256, "idx": page.source_page_index, "rot": page.user_rotation, "mode": mode,
        "steps": steps, "model": model if mode != "local" else None, "effort": effort if mode != "local" else None,
        "prompt": PROMPT_VERSION, "pipeline": PIPELINE_VERSION, "det": local.det_model if local else None, "rec": local.rec_model if local else None,
        "dpi": get_settings().pdf_render_dpi if page.source.kind == "pdf" else None,
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


# --------------------------------------------------------- persistence --
def save_regions(db: Session, page: Page, res: PageOcrResult, job_id: str | None) -> dict:
    """Replace machine regions while preserving user edits.

    A user-edited region is carried over to the new region that overlaps it (IoU>0.5);
    if none does, the edited region is kept unchanged. Machine output never overwrites
    `text_user`.
    """
    old = list(page.regions)
    edited = [r for r in old if r.text_user is not None or r.reviewed]
    carried = 0
    new_regions: list[OcrRegion] = []
    used_old: set[str] = set()
    for i, r in enumerate(res.regions):
        reg = OcrRegion(
            page_id=page.id, region_key=r.key, order_index=i, block_index=r.block_index, block_type=r.block_type,
            polygon=r.quad, position_source=r.position_source if r.quad else "none", text_ocr=r.text,
            text_claude=r.text_claude, text_local=r.text_local, engine=r.engine, uncertain=r.uncertain,
            flags=r.flags, local_score=r.local_score,
        )
        if r.quad:
            bb = quad_bbox(r.quad)
            for o in edited:
                if o.id in used_old or not o.polygon:
                    continue
                if iou_boxes(bb, quad_bbox(o.polygon)) > 0.5:
                    reg.text_user = o.text_user
                    reg.reviewed = o.reviewed
                    used_old.add(o.id)
                    carried += 1
                    break
        new_regions.append(reg)
    kept = [o for o in edited if o.id not in used_old]
    for r in old:
        if r not in kept:
            db.delete(r)
    db.flush()
    for k in kept:
        k.flags = list(k.flags or []) + (["kept_user_edit"] if "kept_user_edit" not in (k.flags or []) else [])
        k.order_index = len(new_regions) + k.order_index
    for reg in new_regions:
        db.add(reg)
    page.ocr_status = "done"
    page.ocr_engine = res.engine
    page.ocr_model = res.model
    page.ocr_error = None
    page.ocr_warnings = res.warnings
    page.ocr_updated_at = utcnow()
    db.add(OcrRun(page_id=page.id, job_id=job_id, engine=res.engine, model=res.model, raw=res.raw))
    return {"regions": len(new_regions), "carried_user_edits": carried, "kept_user_edits": len(kept)}


def process_page(page_id: str, mode: str, job: Job | None, owner_id: str, steps: dict | None = None
                 ) -> dict:
    """Run OCR for one page and persist the result. Raises OcrError/CostLimitExceeded."""
    from app.ocr.pipeline import run_page_ocr

    s = get_settings()
    steps = steps or {}
    params = (job.params if job else {}) or {}
    model = params.get("model") or s.claude_model
    effort = params.get("effort") or s.claude_effort
    storage = get_storage()
    with session_scope() as db:
        page = db.get(Page, page_id)
        if page is None:
            return {"skipped": "page deleted"}
        key = cache_key(page, mode, steps, model, effort)
        cached = db.get(OcrCache, key) if params.get("use_cache", True) else None
        if cached is not None:
            res = PageOcrResult.from_dict(cached.result["result"])
            info = save_regions(db, page, res, job.id if job else None)
            page.preprocess_meta = cached.result.get("meta", {})
            if cached.result.get("processed_key") and storage.exists(cached.result["processed_key"]):
                page.processed_key = cached.result["processed_key"]
            return {**info, "from_cache": True, "api_calls": 0}
        src_key = page.source.storage_key
        page_snapshot = page
        source_bytes = storage.get_bytes(src_key)
        img = page_display_image(page_snapshot, source_bytes)
        doc_id = page.document_id
    bgr = pil_to_bgr(img)
    del img
    ledger = DbLedger(owner_id, job.id if job else None, page_id, job.cost_limit_usd if job else None)
    out = run_page_ocr(bgr, mode, ledger, steps, model, effort)
    ok, buf = cv2.imencode(".jpg", out.processed, [cv2.IMWRITE_JPEG_QUALITY, 88])
    pkey = f"{doc_prefix(doc_id)}proc/{page_id}.jpg"
    if ok:
        storage.put_bytes(pkey, buf.tobytes(), "image/jpeg")
    meta = {"H": out.H, "steps": out.steps, "timings": {k: round(v, 3) for k, v in out.timings.items()},
            "processed_size": [int(out.processed.shape[1]), int(out.processed.shape[0])],
            "display_size": [int(bgr.shape[1]), int(bgr.shape[0])]}
    with session_scope() as db:
        page = db.get(Page, page_id)
        if page is None:
            return {"skipped": "page deleted during OCR"}
        info = save_regions(db, page, out.result, job.id if job else None)
        page.preprocess_meta = meta
        page.processed_key = pkey if ok else None
        if not db.get(OcrCache, key):
            # only cache complete results (no failed re-reads) to avoid freezing errors
            if not any("reread_failed" in r.flags for r in out.result.regions):
                cache_pkey = f"cache/{key}.jpg"
                if ok:
                    storage.put_bytes(cache_pkey, buf.tobytes(), "image/jpeg")
                db.add(OcrCache(key=key, engine=out.result.engine,
                                result={"result": out.result.to_dict(), "meta": meta,
                                        "processed_key": cache_pkey if ok else None}))
    return {**info, "from_cache": False, "api_calls": ledger.calls, "cost_usd": round(ledger.cost, 6)}
