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
from app.models import ApiCall, Job, User, utcnow
from pydantic import BaseModel, Field

from app.schemas.api import LoginIn
from app.ocr.claude_engine import FALLBACK_MODELS
from app.services import cost as cost_svc
from app.version import APP_VERSION

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health():
    return {"ok": True, "version": APP_VERSION}


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
    key = s.anthropic_api_key.get_secret_value() if s.claude_configured else ""  # type: ignore[union-attr]
    return {
        "app_env": s.app_env, "auth_mode": s.auth_mode, "desktop_mode": s.desktop_mode, "version": APP_VERSION,
        "claude": {
            "configured": s.claude_configured,
            "model_choices": cost_svc.model_choices(),
            # desktop app only: last 4 characters so the user can tell which key is in use
            "key_hint": f"…{key[-4:]}" if (s.desktop_mode and key) else None, "model": s.claude_model, "effort": s.claude_effort,
            "fallbacks": (s.claude_fallbacks or None) if s.claude_model in FALLBACK_MODELS else None,
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


class ApiKeyIn(BaseModel):
    api_key: str = Field(min_length=20, max_length=300)


def _desktop_local_only(request: Request) -> None:
    s = get_settings()
    if not s.desktop_mode or not s.pilgibot_config_file:
        raise HTTPException(403, "데스크톱 앱에서만 쓸 수 있는 기능입니다 (서버 버전은 환경변수로 설정합니다).")
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "이 컴퓨터에서만 바꿀 수 있습니다.")


def _save_desktop_config(field: str, env_name: str, value: str | None) -> None:
    """Persist one desktop setting in config.json and apply it to this process."""
    import json
    import os

    from app.ocr import claude_engine

    s = get_settings()
    cfg_path = s.pilgibot_config_file
    data = {}
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except ValueError:
            data = {}
    if value:
        data[field] = value
        os.environ[env_name] = value
    else:
        data.pop(field, None)
        os.environ.pop(env_name, None)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, cfg_path)
    get_settings.cache_clear()
    claude_engine._engines.clear()


@router.post("/settings/api-key")
def set_api_key(body: ApiKeyIn, request: Request, user: User = Depends(current_user)):
    """Desktop app: save the Anthropic API key on this computer (after checking it)."""
    import anthropic

    _desktop_local_only(request)
    key = body.api_key.strip()
    if not key.startswith("sk-ant-"):
        raise HTTPException(400, "Anthropic API 키는 'sk-ant-'로 시작합니다.")
    warning = None
    try:
        anthropic.Anthropic(api_key=key, base_url=get_settings().anthropic_api_url, max_retries=0,
                            timeout=20).models.retrieve(get_settings().claude_model)
    except anthropic.AuthenticationError as e:
        raise HTTPException(400, "API 키가 올바르지 않습니다 (Anthropic 인증 실패).") from e
    except anthropic.PermissionDeniedError as e:
        raise HTTPException(400, "이 API 키로는 모델을 사용할 권한이 없습니다.") from e
    except Exception:
        warning = "인터넷 연결 문제로 키를 확인하지 못했지만 저장했습니다."
    _save_desktop_config("anthropic_api_key", "ANTHROPIC_API_KEY", key)
    return {"ok": True, "warning": warning, "key_hint": f"…{key[-4:]}"}


@router.delete("/settings/api-key")
def delete_api_key(request: Request, user: User = Depends(current_user)):
    _desktop_local_only(request)
    _save_desktop_config("anthropic_api_key", "ANTHROPIC_API_KEY", None)
    return {"ok": True}


class ModelIn(BaseModel):
    model: str = Field(max_length=64)


@router.post("/settings/model")
def set_model(body: ModelIn, request: Request, user: User = Depends(current_user)):
    """Desktop app: choose the Claude model used for new OCR jobs."""
    _desktop_local_only(request)
    allowed = {m["id"] for m in cost_svc.MODEL_CHOICES}
    if body.model not in allowed:
        raise HTTPException(400, "지원하지 않는 모델입니다.")
    _save_desktop_config("claude_model", "OCR_CLAUDE_MODEL", body.model)
    return {"ok": True, "model": body.model}


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


# ----------------------------------------------------------------- update --
@router.get("/update")
def update_info(refresh: int = 0, user: User = Depends(current_user)):
    """Latest desktop release vs. the running version. Only meaningful in desktop mode."""
    s = get_settings()
    if not s.desktop_mode:
        return {"supported": False, "current": APP_VERSION}
    from app.services import updater

    return {"supported": True, **updater.check(refresh=bool(refresh)), "state": updater.status()}


@router.get("/update/status")
def update_status(user: User = Depends(current_user)):
    from app.services import updater

    return updater.status()


@router.post("/update/install")
def update_install(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Download the newest installer, verify it, run it silently and restart (Windows installer builds)."""
    _desktop_local_only(request)
    from app.services import updater

    info = updater.check()
    if info["error"] and not info["latest"]:
        raise HTTPException(503, info["error"])
    if not info["available"]:
        raise HTTPException(400, "이미 최신 버전입니다.")
    if not info["can_self_update"]:
        raise HTTPException(400, "이 설치 방식에서는 자동 업데이트를 지원하지 않습니다. Releases에서 새 버전을 받아 설치하세요.")
    active = db.scalar(select(func.count()).select_from(Job).where(Job.status.in_(("queued", "running")))) or 0
    if active:
        raise HTTPException(409, f"진행 중인 작업이 {active}개 있습니다. 끝난 뒤(또는 일시정지한 뒤) 업데이트하세요.")
    return updater.start_install(info)


# ------------------------------------------------------------- evaluation --
@router.post("/evaluations")
async def evaluate(image: UploadFile = File(...), ground_truth: str = Form(default=""),
                   engines: str = Form(default="local,ai,hybrid"), models: str = Form(default=""),
                   page: int = Form(default=1), user: User = Depends(current_user)):
    """Run several engines on one image and compare. CER/WER only with ground truth.

    A PDF is accepted too: the given page (1-based, default 1) is rendered and evaluated
    like an image. The evaluation always works on exactly one page.
    """
    from starlette.concurrency import run_in_threadpool

    from app.evaluation.runner import PdfPageError, evaluate_image, pdf_page_image
    from app.services.ingest import sniff_mime

    s = get_settings()
    limiter.hit(f"eval:{user.id}", 20, 3600)
    data = await image.read(s.max_file_bytes + 1)
    if len(data) > s.max_file_bytes:
        raise HTTPException(400, f"파일이 너무 큽니다 (최대 {s.max_file_mb}MB).")
    mime = sniff_mime(data[:2048])
    source = None
    if mime == "application/pdf":
        try:
            data, page_count = await run_in_threadpool(pdf_page_image, data, page, s.pdf_render_dpi,
                                                       s.max_pdf_pages_per_file)
        except PdfPageError as e:
            raise HTTPException(400, str(e)) from e
        source = {"kind": "pdf", "page": page, "pages": page_count}
    elif mime not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(400, "JPG/PNG/WEBP 이미지 또는 PDF만 평가할 수 있습니다.")
    names = [e.strip() for e in engines.split(",") if e.strip() in ("local", "ai", "hybrid")]
    if not names:
        raise HTTPException(400, "engines: local, ai, hybrid 중 선택")
    if any(n != "local" for n in names) and not s.claude_configured:
        raise HTTPException(400, "Claude API 키가 없어 ai/hybrid 평가를 할 수 없습니다.")
    allowed = {m["id"] for m in cost_svc.MODEL_CHOICES} | {s.claude_model}
    model_list = [m.strip() for m in models.split(",") if m.strip() in allowed] or [s.claude_model]
    result = await run_in_threadpool(evaluate_image, data, ground_truth, names, user.id, model_list)
    result["source"] = source or {"kind": "image", "page": 1, "pages": 1}
    return result
