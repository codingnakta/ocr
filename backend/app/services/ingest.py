"""Upload validation and ingestion (images and PDFs -> Pages)."""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass

import pymupdf
from PIL import Image, ImageOps
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.imaging.io import exif_info, make_thumbnail, to_rgb
from app.models import Document, Page, SourceFile, new_id
from app.services.storage import doc_prefix, get_storage

ALLOWED = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "application/pdf": "pdf"}


class UploadError(ValueError):
    pass


def sniff_mime(head: bytes) -> str | None:
    """Detect the real file type from magic bytes (the client's MIME is not trusted)."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if b"%PDF-" in head[:1024]:
        return "application/pdf"
    if head[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc"):
        return "image/heic"
    return None


def sanitize_filename(name: str | None) -> str:
    name = unicodedata.normalize("NFC", name or "")
    name = name.replace("\\", "/").split("/")[-1]  # drop any path component
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '<>:"|?*')
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name or "upload")[:120]


@dataclass
class PdfPageInfo:
    index: int
    width: int
    height: int
    content_type: str  # text | mixed | image_only | empty
    text_chars: int


def analyze_pdf_page(page: pymupdf.Page, dpi: int) -> PdfPageInfo:
    text = page.get_text("text") or ""
    chars = len("".join(text.split()))
    area = max(page.rect.width * page.rect.height, 1.0)
    covered = 0.0
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & page.rect
        covered += max(0.0, r.width * r.height)
    coverage = min(1.0, covered / area)
    invisible = 0
    if chars:
        try:
            for span in page.get_texttrace():
                if span.get("type") == 3:
                    invisible += len(span.get("chars", ()))
        except Exception:
            pass
    drawings = 0
    if chars < 30 and coverage < 0.3:
        try:
            drawings = len(page.get_drawings())
        except Exception:
            drawings = 0
    if chars >= 30 and coverage >= 0.5 and invisible >= 0.8 * chars:
        ctype = "text"  # already OCR'd (invisible layer over a scan)
    elif chars >= 30 and coverage < 0.5:
        ctype = "text"
    elif chars >= 30:
        ctype = "mixed"
    elif coverage >= 0.05 or drawings >= 20:
        ctype = "image_only"  # scan, photo or vector ink (e.g. note-app export)
    else:
        ctype = "empty"
    r = page.rect  # displayed (rotation applied)
    return PdfPageInfo(page.number, int(round(r.width * dpi / 72)), int(round(r.height * dpi / 72)), ctype, chars)


def ingest_file(db: Session, doc: Document, filename: str | None, data: bytes, client_mime: str | None = None
                ) -> list[Page]:
    s = get_settings()
    if not data:
        raise UploadError("빈 파일입니다.")
    if len(data) > s.max_file_bytes:
        raise UploadError(f"파일이 너무 큽니다 (최대 {s.max_file_mb}MB).")
    mime = sniff_mime(data[:2048])
    if mime == "image/heic":
        raise UploadError("HEIC 형식은 지원하지 않습니다. 카메라 설정을 '호환성 우선'으로 하거나 JPG로 변환해 주세요.")
    if mime not in ALLOWED:
        raise UploadError("허용되지 않는 파일 형식입니다 (JPG, PNG, WEBP, PDF만 가능).")
    current = db.scalar(select(func.count()).select_from(Page).where(Page.document_id == doc.id)) or 0
    name = sanitize_filename(filename)
    sha = hashlib.sha256(data).hexdigest()
    storage = get_storage()
    file_id = new_id()
    key = f"{doc_prefix(doc.id)}src/{file_id}.{ALLOWED[mime]}"
    max_pos = db.scalar(select(func.max(Page.position)).where(Page.document_id == doc.id))
    pos = (max_pos + 1) if max_pos is not None else 0
    pages: list[Page] = []

    if mime == "application/pdf":
        try:
            pdf = pymupdf.open(stream=data, filetype="pdf")
        except Exception as e:
            raise UploadError("PDF를 열 수 없습니다 (손상되었거나 올바른 PDF가 아닙니다).") from e
        with pdf:
            if pdf.needs_pass or pdf.is_encrypted:
                raise UploadError("암호가 걸린 PDF는 지원하지 않습니다.")
            n = pdf.page_count
            if n == 0:
                raise UploadError("페이지가 없는 PDF입니다.")
            if n > s.max_pdf_pages_per_file:
                raise UploadError(f"PDF 페이지가 너무 많습니다 (파일당 최대 {s.max_pdf_pages_per_file}쪽).")
            if current + n > s.max_pages_per_document:
                raise UploadError(f"문서당 최대 {s.max_pages_per_document}쪽까지 가능합니다.")
            sf = SourceFile(id=file_id, document_id=doc.id, kind="pdf", filename=name, storage_key=key, mime=mime,
                            size_bytes=len(data), sha256=sha, page_count=n)
            infos = []
            thumbs = []
            for i in range(n):
                try:
                    pg = pdf[i]
                    info = analyze_pdf_page(pg, s.pdf_render_dpi)
                    pix = pg.get_pixmap(dpi=40, alpha=False, colorspace=pymupdf.csRGB)
                    thumbs.append(make_thumbnail(Image.frombytes("RGB", (pix.width, pix.height), pix.samples)))
                except Exception as e:
                    raise UploadError(f"PDF {i + 1}쪽을 읽는 중 오류가 발생했습니다 (손상된 페이지).") from e
                infos.append(info)
        storage.put_bytes(key, data, mime)
        db.add(sf)
        for info, thumb in zip(infos, thumbs):
            page = Page(document_id=doc.id, source_file_id=file_id, source_page_index=info.index, position=pos,
                        label=f"{name} p.{info.index + 1}", base_width=info.width, base_height=info.height,
                        content_type=info.content_type, existing_text_chars=info.text_chars)
            page.id = new_id()
            tkey = f"{doc_prefix(doc.id)}thumb/{page.id}.jpg"
            storage.put_bytes(tkey, thumb, "image/jpeg")
            page.thumb_key = tkey
            if info.content_type in ("text", "empty"):
                page.ocr_status = "skipped"
            db.add(page)
            pages.append(page)
            pos += 1
        return pages

    # ---- images
    if current + 1 > s.max_pages_per_document:
        raise UploadError(f"문서당 최대 {s.max_pages_per_document}쪽까지 가능합니다.")
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()  # structural check
        img = Image.open(io.BytesIO(data))
        rot, _ = exif_info(img)
        if img.format == "JPEG":
            img.draft("RGB", (1600, 1600))  # fast decode for thumbnail only
        img.load()
        full_w, full_h = Image.open(io.BytesIO(data)).size
    except Image.DecompressionBombError as e:
        raise UploadError("이미지 해상도가 너무 큽니다.") from e
    except Exception as e:
        raise UploadError("이미지를 읽을 수 없습니다 (손상된 파일).") from e
    if rot in (90, 270):
        full_w, full_h = full_h, full_w
    if full_w < 50 or full_h < 50:
        raise UploadError("이미지가 너무 작습니다 (최소 50×50).")
    thumb = make_thumbnail(to_rgb(ImageOps.exif_transpose(img)))
    sf = SourceFile(id=file_id, document_id=doc.id, kind="image", filename=name, storage_key=key, mime=mime,
                    size_bytes=len(data), sha256=sha, page_count=1)
    storage.put_bytes(key, data, mime)
    db.add(sf)
    page = Page(id=new_id(), document_id=doc.id, source_file_id=file_id, position=pos, label=name,
                base_width=full_w, base_height=full_h, exif_rotation=rot, content_type="image")
    tkey = f"{doc_prefix(doc.id)}thumb/{page.id}.jpg"
    storage.put_bytes(tkey, thumb, "image/jpeg")
    page.thumb_key = tkey
    db.add(page)
    return [page]
