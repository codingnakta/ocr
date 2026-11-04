"""Application settings, loaded from environment variables (and an optional .env file).

Secrets such as ANTHROPIC_API_KEY live only on the backend. Nothing here is ever
sent to the browser except the explicitly whitelisted fields in /api/settings/public.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_DIR / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- environment -------------------------------------------------------
    app_env: Literal["development", "production", "test"] = "development"
    app_name: str = "필기봇 OCR Studio"

    # ---- auth --------------------------------------------------------------
    # none     : no login (allowed only in development/test)
    # password : username/password login with an HttpOnly session cookie
    auth_mode: Literal["none", "password"] = "none"
    session_secret: SecretStr = SecretStr("dev-insecure-session-secret-change-me")
    session_max_age_hours: int = 24 * 7
    # Bootstrap admin, created at startup if missing (password mode only).
    admin_username: str | None = None
    admin_password: SecretStr | None = None
    cookie_secure: bool = False

    # ---- storage / database ------------------------------------------------
    database_url: str = f"sqlite:///{(BACKEND_DIR / 'data' / 'pilgibot.db').as_posix()}"
    storage_backend: Literal["local", "s3"] = "local"
    storage_dir: Path = BACKEND_DIR / "data" / "files"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    s3_prefix: str = "pilgibot/"
    work_dir: Path = BACKEND_DIR / "data" / "work"
    retention_days: int = 0  # 0 = keep forever (dev). Production default set below.

    # ---- upload limits -----------------------------------------------------
    max_file_mb: int = 40
    max_pages_per_document: int = 500
    max_pdf_pages_per_file: int = 300
    max_image_pixels: int = 80_000_000  # decompression-bomb guard
    max_documents_per_user: int = 200

    # ---- Claude ------------------------------------------------------------
    # Env names use an OCR_ prefix so they never collide with variables of other
    # Claude tools on the same machine (e.g. Claude Code sets CLAUDE_EFFORT).
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY"))
    anthropic_api_url: str = Field(default="https://api.anthropic.com", validation_alias="OCR_ANTHROPIC_API_URL")
    claude_model: str = Field(default="claude-opus-5-5", validation_alias="OCR_CLAUDE_MODEL")
    # Thinking cannot be disabled on Opus 5.5; effort is the cost/quality lever.
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = Field(
        default="medium", validation_alias="OCR_CLAUDE_EFFORT")
    claude_max_tokens: int = Field(default=16000, validation_alias="OCR_CLAUDE_MAX_TOKENS")
    claude_timeout_s: float = Field(default=180.0, validation_alias="OCR_CLAUDE_TIMEOUT_S")
    claude_max_retries: int = Field(default=2, validation_alias="OCR_CLAUDE_MAX_RETRIES")
    # Server-side refusal fallback ("default" routing). Empty string disables it.
    claude_fallbacks: str = Field(default="default", validation_alias="OCR_CLAUDE_FALLBACKS")
    claude_long_edge_px: int = Field(default=2576, validation_alias="OCR_CLAUDE_LONG_EDGE_PX")
    claude_max_visual_tokens: int = Field(default=4784, validation_alias="OCR_CLAUDE_MAX_VISUAL_TOKENS")

    # ---- cost control ------------------------------------------------------
    default_job_cost_limit_usd: float = 5.0
    daily_cost_limit_usd: float = 30.0  # per user, across jobs
    # Optional pricing override, e.g. "claude-opus-5-5=4,20,0.2,5"
    # (input, output, cache read, cache write 5m — USD per million tokens)
    claude_pricing_override: str | None = Field(default=None, validation_alias="OCR_CLAUDE_PRICING_OVERRIDE")

    # ---- local OCR (RapidOCR) ---------------------------------------------
    local_ocr_enabled: bool = True
    local_ocr_rec_lang: str = "korean"
    local_ocr_model_dir: Path = BACKEND_DIR / "models_data"
    # Explicit model paths (optional). When unset RapidOCR's own model manager
    # downloads the official models (ModelScope) on first use.
    local_ocr_det_model: Path | None = None
    local_ocr_rec_model: Path | None = None
    local_ocr_rec_keys: Path | None = None
    local_ocr_cls_model: Path | None = None
    orientation_model: Path | None = None

    # ---- processing --------------------------------------------------------
    embedded_worker: bool = True  # run the queue worker inside the API process
    worker_concurrency: int = 2
    # Single worker process (default compose): on start, every task left 'running' is an
    # orphan of the previous run and is re-queued at once. With several worker
    # processes set this to false: only locks older than TASK_LOCK_TIMEOUT_S are taken.
    worker_recover_all: bool = True
    worker_poll_interval_s: float = 1.0
    task_max_attempts: int = 3
    task_lock_timeout_s: int = 900
    pdf_render_dpi: int = 200

    # ---- rate limits (per user) -------------------------------------------
    rate_limit_uploads_per_min: int = 600
    rate_limit_ocr_starts_per_hour: int = 30
    rate_limit_login_per_15min: int = 10

    cors_origins: list[str] = Field(default_factory=list)

    # ---- desktop app (Windows exe) -------------------------------------------
    # Serve the statically exported frontend (frontend/out) from this server.
    frontend_dir: Path | None = None
    # Desktop mode: single local user, API key entered in the UI and stored in
    # PILGIBOT_CONFIG_FILE (only requests from this computer may change it).
    desktop_mode: bool = False
    pilgibot_config_file: Path | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.app_env == "production":
            if self.auth_mode == "none":
                raise ValueError("AUTH_MODE=none is not allowed when APP_ENV=production")
            if self.session_secret.get_secret_value().startswith("dev-insecure"):
                raise ValueError("SESSION_SECRET must be set to a random value in production")
            if self.retention_days == 0:
                self.retention_days = 14
        return self

    @property
    def is_dev(self) -> bool:
        return self.app_env != "production"

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024

    @property
    def claude_configured(self) -> bool:
        return bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value().strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
