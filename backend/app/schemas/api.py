"""Request bodies (responses are plain dicts built in app/api/serializers.py)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Mode = Literal["ai", "local", "hybrid"]


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class DocumentCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    page_size_mode: Literal["original", "a4"] | None = None
    pdf_image_source: Literal["original", "enhanced"] | None = None


class PageOrder(BaseModel):
    page_ids: list[str] = Field(max_length=5000)


class PagePatch(BaseModel):
    rotate: Literal[90, 180, 270, -90] | None = Field(default=None, description="clockwise rotation delta")
    label: str | None = Field(default=None, max_length=255)


class OcrRequest(BaseModel):
    mode: Mode = "hybrid"
    page_ids: list[str] | None = None
    rerun: bool = False
    cost_limit_usd: float | None = Field(default=None, ge=0, le=10000)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    preprocess: dict[str, bool] | None = None
    use_cache: bool = True


class EstimateRequest(BaseModel):
    mode: Mode = "hybrid"
    page_ids: list[str] | None = None
    rerun: bool = False


class RegionEdit(BaseModel):
    id: str
    text_user: str | None = Field(default=None, max_length=20000)
    revert: bool = False  # true -> drop the user's text, use machine text again
    reviewed: bool | None = None


class RegionCreate(BaseModel):
    polygon: list[list[float]] = Field(min_length=4, max_length=4)
    text_user: str = Field(max_length=20000)


class OcrPatch(BaseModel):
    regions: list[RegionEdit] = Field(default_factory=list, max_length=2000)
    create: list[RegionCreate] = Field(default_factory=list, max_length=200)
    delete: list[str] = Field(default_factory=list, max_length=2000)


class RegionReocr(BaseModel):
    # normalized rectangle [x0, y0, x1, y1] in display coordinates, or an existing region
    rect: list[float] | None = Field(default=None, min_length=4, max_length=4)
    region_id: str | None = None
    engine: Literal["claude", "local"] = "claude"
    create_region: bool = False


class JobResume(BaseModel):
    cost_limit_usd: float | None = Field(default=None, ge=0, le=10000)


class ExportRequest(BaseModel):
    format: Literal["pdf", "txt", "md", "json"] = "pdf"
    size_mode: Literal["original", "a4"] | None = None
    image_source: Literal["original", "enhanced"] | None = None
