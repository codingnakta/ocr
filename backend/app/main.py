"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import documents, jobs, system
from app.core.config import get_settings
from app.core.db import init_db
from app.core.logging import install_redaction
from app.core.security import CSRF_HEADER, ensure_bootstrap_users

log = logging.getLogger("pilgibot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    install_redaction()
    s = get_settings()
    init_db()
    ensure_bootstrap_users()
    worker = None
    if s.embedded_worker:
        from app.workers.worker import Worker, recover

        recover(stale_only=False)  # single process: anything 'running' is an orphan
        worker = Worker()
        worker.start()
        log.info("embedded worker started (%s threads)", worker.concurrency)
    if not s.claude_configured:
        log.warning("ANTHROPIC_API_KEY not set: AI/hybrid OCR disabled")
    yield
    if worker:
        worker.stop()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="필기봇 OCR Studio API", version="1.0.0", lifespan=lifespan,
                  docs_url="/api/docs" if s.is_dev else None, redoc_url=None,
                  openapi_url="/api/openapi.json" if s.is_dev else None)
    if s.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
            if request.headers.get(CSRF_HEADER) != "1":
                return JSONResponse({"detail": "CSRF 헤더가 없습니다."}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        errs = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()][:10]
        return JSONResponse({"detail": "요청 형식이 올바르지 않습니다.", "errors": errs}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "서버 오류가 발생했습니다."}, status_code=500)

    app.include_router(system.router)
    app.include_router(documents.router)
    app.include_router(jobs.router)
    if s.frontend_dir and Path(s.frontend_dir).is_dir():
        # Desktop build: the exported Next.js app is served by this same server.
        app.mount("/", StaticFiles(directory=str(s.frontend_dir), html=True), name="frontend")
    return app


app = create_app()
