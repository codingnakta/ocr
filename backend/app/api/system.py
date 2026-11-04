"""Auth, settings/admin info, usage, health, and OCR evaluation."""

from __future__ import annotations

import time
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import COOKIE, current_user, limiter, make_session, verify_password
from app.models import ApiCall, User, utcnow
from app.schemas.api import LoginIn
from app.services import cost as cost_svc

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health():
    return {"ok": True}


# ------------------------------------------------------------------ auth --
@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    if s.auth_mode == "none":
        return {"user": {"username": "local", "is_admin": True}, "auth_mode": "none"}
    ip = request.client.host if request.client else "?"
    limiter.hit(f"login:{ip}", s.rate_limit_login_per_15min, 900)
    u = db.scalar(select(User).where(User.username == body.username))
    if u is None or not verify_password(body.password, u.password_hash):
        time.sleep(0.5)
        raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다.")
    response.set_cookie(COOKIE, make_session(u), max_age=s.session_max_age_hours * 3600, httponly=True,
                        secure=s.cookie_secure, samesite="lax", path="/")
    return {"user": {"username": u.username, "is_admin": u.is_admin}, "auth_mode": s.auth_mode}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    try:
        u = current_user(request, db)
    except HTTPException:
        return {"authenticated": False, "auth_mode": s.auth_mode}
    return {"authenticated": True, "auth_mode": s.auth_mode, "user": {"username": u.username, "is_admin": u.is_admin}}


# --------------------------------------------------------------- settings --
@router.get("/settings")
def settings_info(user: User = Depends(current_user)):
    from app.ocr.local_engine import local_info

    s = get_settings()
    price, known = cost_svc.price_for(s.claude_model)
    try:
        local = local_info()
    except Exception as e:  # model files missing etc.
        local = {"available": False, "note": f"로컬 OCR 초기화 실패: {type(e).__name__}"}
    return {
        "app_env": s.app_env, "auth_mode": s.auth_mode,
        "claude": {
            "configured": s.claude_configured, "model": s.claude_model, "effort": s.claude_effort,
            "fallbacks": s.claude_fallbacks or None,
            "pricing": {"input": price.input, "output": price.output, "cache_read": price.cache_read} if price else None,
            "pricing_known": known, "pricing_checked": cost_svc.PRICING_CHECKED,
            "image_limits": {"long_edge_px": s.claude_long_edge_px, "max_visual_tokens": s.claude_max_visual_tokens},
        },
        "local_ocr": local,
        "limits": {
            "max_file_mb": s.max_file_mb, "max_pages_per_document": s.max_pages_per_document,
            "max_pdf_pages_per_file": s.max_pdf_pages_per_file,
            "default_job_cost_limit_usd": s.default_job_cost_limit_usd,
            "daily_cost_limit_usd": s.daily_cost_limit_usd, "retention_days": s.retention_days,
        },
        "notices": [
            "AI/하이브리드 모드는 페이지 이미지를 Anthropic(Claude API)으로 전송합니다. 로컬 모드는 외부로 전송하지 않습니다.",
            "API 크레딧 잔액은 이 앱에서 조회할 수 없습니다. Anthropic Console의 Billing에서 확인하세요.",
            "Claude 구독(Pro/Max) 사용 한도와 API 크레딧은 별개입니다. 이 앱은 API 키의 조직 과금을 사용합니다.",
        ],
    }


@router.get("/usage")
def usage(days: int = 30, db: Session = Depends(get_db), user: User = Depends(current_user)):
    from app.services.ocr_service import spent_usd, today_start

    s = get_settings()
    days = max(1, min(days, 365))
    since = utcnow() - timedelta(days=days)
    q = select(ApiCall).where(ApiCall.started_at >= since)
    if not user.is_admin or s.auth_mode != "none":
        q = q.where(ApiCall.owner_id == user.id)
    rows = db.scalars(q.order_by(ApiCall.started_at.desc()).limit(5000)).all()
    by_model: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    for c in rows:
        m = by_model.setdefault(c.served_model or c.model, {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                                            "cost_usd": 0.0, "failed": 0, "uncertain": 0})
        d = by_day.setdefault(c.started_at.date().isoformat(), {"calls": 0, "cost_usd": 0.0})
        for agg in (m, d):
            agg["calls"] += 1
            agg["cost_usd"] += c.cost_usd or 0.0
        m["input_tokens"] += c.input_tokens
        m["output_tokens"] += c.output_tokens
        m["failed"] += c.status == "failed"
        m["uncertain"] += c.status == "uncertain"
    pages = db.scalar(select(func.count(func.distinct(ApiCall.page_id))).where(
        ApiCall.started_at >= since, ApiCall.owner_id == user.id, ApiCall.status == "succeeded")) or 0
    return {
        "days": days, "pages_processed": pages,
        "total_cost_usd": round(sum(c.cost_usd or 0 for c in rows), 6),
        "uncertain_calls": sum(1 for c in rows if c.status == "uncertain"),
        "uncertain_cost_estimate_usd": round(sum(c.estimated_cost_usd for c in rows if c.status == "uncertain"), 6),
        "today_spent_usd": round(spent_usd(db, owner_id=user.id, since=today_start()), 6),
        "daily_limit_usd": s.daily_cost_limit_usd,
        "by_model": {k: {**v, "cost_usd": round(v["cost_usd"], 6)} for k, v in by_model.items()},
        "by_day": {k: {**v, "cost_usd": round(v["cost_usd"], 6)} for k, v in sorted(by_day.items())},
        "recent": [{
            "id": c.id, "at": c.started_at.isoformat(), "purpose": c.purpose, "model": c.served_model or c.model,
            "status": c.status, "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
            "thinking_tokens": c.thinking_tokens, "cost_usd": round(c.cost_usd, 6),
            "estimated_cost_usd": round(c.estimated_cost_usd, 6), "retry": c.sdk_retries, "error": c.error,
            "job_id": c.job_id,
        } for c in rows[:100]],
        "note": "비용은 API 응답의 실제 토큰 사용량 × 공식 단가로 계산합니다. 'uncertain'은 중단으로 결과를 받지 못해 "
                "과금 여부를 알 수 없는 호출이며 예상 비용으로 표시됩니다.",
    }


# ------------------------------------------------------------- evaluation --
@router.post("/evaluations")
async def evaluate(image: UploadFile = File(...), ground_truth: str = Form(default=""),
                   engines: str = Form(default="local,ai,hybrid"), user: User = Depends(current_user)):
    """Run several engines on one image and compare. CER/WER only with ground truth."""
    from starlette.concurrency import run_in_threadpool

    from app.evaluation.runner import evaluate_image
    from app.services.ingest import sniff_mime

    s = get_settings()
    limiter.hit(f"eval:{user.id}", 20, 3600)
    data = await image.read(s.max_file_bytes + 1)
    if len(data) > s.max_file_bytes or sniff_mime(data[:2048]) not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(400, "JPG/PNG/WEBP 이미지만 평가할 수 있습니다.")
    names = [e.strip() for e in engines.split(",") if e.strip() in ("local", "ai", "hybrid")]
    if not names:
        raise HTTPException(400, "engines: local, ai, hybrid 중 선택")
    if any(n != "local" for n in names) and not s.claude_configured:
        raise HTTPException(400, "Claude API 키가 없어 ai/hybrid 평가를 할 수 없습니다.")
    return await run_in_threadpool(evaluate_image, data, ground_truth, names, user.id)
