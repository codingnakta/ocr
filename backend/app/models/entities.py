"""SQLAlchemy ORM models.

Coordinates of OCR regions are stored *normalized* (0..1) relative to the page as it
is displayed (after EXIF orientation and the user's rotation). That keeps them
independent of render DPI and lets the PDF writer map them onto any page size.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(200))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="새 문서")
    # export options
    page_size_mode: Mapped[str] = mapped_column(String(16), default="original")  # original | a4
    pdf_image_source: Mapped[str] = mapped_column(String(16), default="original")  # original | enhanced
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    pages: Mapped[list["Page"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Page.position"
    )
    files: Mapped[list["SourceFile"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class SourceFile(Base):
    __tablename__ = "source_files"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # image | pdf
    filename: Mapped[str] = mapped_column(String(255))  # sanitized display name only
    storage_key: Mapped[str] = mapped_column(String(400))
    mime: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(back_populates="files")


class Page(Base):
    __tablename__ = "pages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    source_file_id: Mapped[str] = mapped_column(ForeignKey("source_files.id", ondelete="CASCADE"))
    source_page_index: Mapped[int] = mapped_column(Integer, default=0)  # for PDF sources
    position: Mapped[int] = mapped_column(Integer, index=True)
    label: Mapped[str] = mapped_column(String(255), default="")
    # Size of the source as displayed *before* user rotation (EXIF already applied), px.
    base_width: Mapped[int] = mapped_column(Integer)
    base_height: Mapped[int] = mapped_column(Integer)
    exif_rotation: Mapped[int] = mapped_column(Integer, default=0)  # CW degrees applied from file pixels
    user_rotation: Mapped[int] = mapped_column(Integer, default=0)  # 0/90/180/270 clockwise
    # PDF analysis: image | text | mixed | image_only | empty
    content_type: Mapped[str] = mapped_column(String(16), default="image")
    existing_text_chars: Mapped[int] = mapped_column(Integer, default=0)
    thumb_key: Mapped[str | None] = mapped_column(String(400))
    # OCR state
    ocr_status: Mapped[str] = mapped_column(String(16), default="none")  # none|done|failed|skipped
    ocr_engine: Mapped[str | None] = mapped_column(String(32))
    ocr_model: Mapped[str | None] = mapped_column(String(64))
    ocr_error: Mapped[str | None] = mapped_column(Text)
    ocr_warnings: Mapped[list] = mapped_column(JSON, default=list)
    preprocess_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    processed_key: Mapped[str | None] = mapped_column(String(400))  # preprocessed image (OCR input)
    ocr_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    document: Mapped[Document] = relationship(back_populates="pages")
    source: Mapped[SourceFile] = relationship()
    regions: Mapped[list["OcrRegion"]] = relationship(
        back_populates="page", cascade="all, delete-orphan", order_by="OcrRegion.order_index"
    )

    @property
    def rotation_total(self) -> int:
        return (self.exif_rotation + self.user_rotation) % 360

    @property
    def display_size(self) -> tuple[int, int]:
        if self.user_rotation in (90, 270):
            return self.base_height, self.base_width
        return self.base_width, self.base_height


class OcrRegion(Base):
    """One recognized line/region of a page.

    text_ocr   : machine result used by default (Claude or local, after hybrid checks)
    text_claude: raw Claude transcription for this region (preserved, never edited)
    text_local : raw local OCR transcription (preserved)
    text_user  : user's correction; once set, machine re-runs never overwrite it
    polygon    : normalized [[x,y]*4] in display coordinates, or None if the text could
                 not be aligned to a position (then it is NOT placed in the PDF layer)
    """

    __tablename__ = "ocr_regions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    page_id: Mapped[str] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    region_key: Mapped[str] = mapped_column(String(16))
    order_index: Mapped[int] = mapped_column(Integer)
    block_index: Mapped[int] = mapped_column(Integer, default=0)
    block_type: Mapped[str] = mapped_column(String(16), default="paragraph")
    polygon: Mapped[list | None] = mapped_column(JSON)
    position_source: Mapped[str] = mapped_column(String(24), default="local_det")
    text_ocr: Mapped[str] = mapped_column(Text, default="")
    text_claude: Mapped[str | None] = mapped_column(Text)
    text_local: Mapped[str | None] = mapped_column(Text)
    text_user: Mapped[str | None] = mapped_column(Text)
    engine: Mapped[str] = mapped_column(String(24))
    uncertain: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    local_score: Mapped[float | None] = mapped_column(Float)  # RapidOCR's own score, if any
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    page: Mapped[Page] = relationship(back_populates="regions")

    @property
    def text(self) -> str:
        return self.text_user if self.text_user is not None else self.text_ocr


class OcrRun(Base):
    """Raw engine output per page run, kept for audit / comparison."""

    __tablename__ = "ocr_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    page_id: Mapped[str] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(32), index=True)
    engine: Mapped[str] = mapped_column(String(24))
    model: Mapped[str | None] = mapped_column(String(64))
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="ocr")  # ocr | export
    # queued | running | paused | cancelled | completed | completed_with_errors | failed
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    mode: Mapped[str] = mapped_column(String(16), default="hybrid")  # ai | local | hybrid
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    cost_limit_usd: Mapped[float | None] = mapped_column(Float)
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tasks: Mapped[list["PageTask"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class PageTask(Base):
    __tablename__ = "page_tasks"
    __table_args__ = (
        UniqueConstraint("job_id", "page_id", name="uq_task_job_page"),
        Index("ix_tasks_status_job", "status", "job_id"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[str] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)  # processing order (= page position at job start)
    # pending | running | done | failed | skipped | cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    from_cache: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship(back_populates="tasks")


class ApiCall(Base):
    """Ledger of every Claude request. Written *before* the request is sent
    (status=started) so that a crash mid-request is visible as a possibly
    billed call (status=uncertain) instead of silently disappearing."""

    __tablename__ = "api_calls"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str | None] = mapped_column(String(32), index=True)
    job_id: Mapped[str | None] = mapped_column(String(32), index=True)
    page_id: Mapped[str | None] = mapped_column(String(32), index=True)
    purpose: Mapped[str] = mapped_column(String(24))  # page | regions | evaluation | reread
    model: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="started")  # started|succeeded|failed|uncertain
    request_hash: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    thinking_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)  # pre-call estimate
    sdk_retries: Mapped[int] = mapped_column(Integer, default=0)
    served_model: Mapped[str | None] = mapped_column(String(64))
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    request_id: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OcrCache(Base):
    """Successful engine results keyed by (image hash, engine params, model, prompt version)."""

    __tablename__ = "ocr_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    engine: Mapped[str] = mapped_column(String(24))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Export(Base):
    __tablename__ = "exports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[str] = mapped_column(String(32), index=True)
    format: Mapped[str] = mapped_column(String(8))  # pdf | txt | md | json
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|failed
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    storage_key: Mapped[str | None] = mapped_column(String(400))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    report: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. text layer verification
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
