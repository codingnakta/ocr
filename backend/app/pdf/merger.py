"""Build PDF pages from images / existing PDF pages, preserving the original pixels.

* JPEG and PNG bytes are embedded as-is (no recompression). Rotation (EXIF + user)
  is applied with the image placement matrix, not by re-encoding pixels.
* WEBP (not a PDF image format) is converted losslessly to PNG.
* Mirrored EXIF orientations (rare) are re-encoded after transposition.
* Existing PDF pages are copied unchanged (`insert_pdf`), never overwritten.
"""

from __future__ import annotations

import io

import pymupdf
from PIL import Image, ImageOps

from app.imaging.io import exif_info, to_rgb

A4 = (595.276, 841.89)


def page_size_for(display_w: int, display_h: int, mode: str) -> tuple[float, float, pymupdf.Rect]:
    """Return (page_w, page_h, image_rect) in points."""
    if mode == "a4":
        pw, ph = A4 if display_h >= display_w else (A4[1], A4[0])
        margin = 0.0
        s = min((pw - 2 * margin) / display_w, (ph - 2 * margin) / display_h)
        iw, ih = display_w * s, display_h * s
        x0, y0 = (pw - iw) / 2, (ph - ih) / 2
        return pw, ph, pymupdf.Rect(x0, y0, x0 + iw, y0 + ih)
    # original aspect ratio, long edge = A4 long edge (resolution is untouched)
    s = A4[1] / max(display_w, display_h)
    pw, ph = display_w * s, display_h * s
    return pw, ph, pymupdf.Rect(0, 0, pw, ph)


def primary_jpeg(data: bytes, img: Image.Image) -> bytes | None:
    """MPO (e.g. iPhone photos: JPEG + gain map/depth frames) -> the primary JPEG frame,
    byte-identical, without the trailing auxiliary images."""
    try:
        mp = getattr(img, "mpinfo", None) or img.info.get("mpinfo") or {}
        entries = mp.get(0xB002) or []
        size = int(entries[0]["Size"]) if entries else 0
    except Exception:
        size = 0
    if size and size <= len(data) and data[size - 2:size] == b"\xff\xd9":
        return data[:size]
    return None


def prepare_image_stream(data: bytes, mime: str, user_rotation: int) -> tuple[bytes, int]:
    """Bytes to embed and the clockwise rotation to apply at placement time."""
    img = Image.open(io.BytesIO(data))
    rot, mirrored = exif_info(img)
    fmt = (img.format or "").upper()
    if fmt == "MPO":
        prim = primary_jpeg(data, img)
        if prim is not None:
            data, fmt = prim, "JPEG"
    if fmt in ("JPEG", "PNG") and not mirrored:
        if fmt == "PNG" and img.mode in ("RGBA", "LA", "P", "I;16", "I"):
            # transparency / exotic modes: flatten once, losslessly
            buf = io.BytesIO()
            to_rgb(img).save(buf, "PNG")
            return buf.getvalue(), (rot + user_rotation) % 360
        if fmt == "JPEG" and img.mode == "CMYK":
            buf = io.BytesIO()
            to_rgb(img).save(buf, "JPEG", quality=95)
            return buf.getvalue(), (rot + user_rotation) % 360
        return data, (rot + user_rotation) % 360
    # WEBP, mirrored EXIF, others: decode once and store losslessly as PNG
    img = to_rgb(ImageOps.exif_transpose(img))
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.save(buf, "JPEG", quality=97, subsampling=0)
    else:
        img.save(buf, "PNG")
    return buf.getvalue(), user_rotation % 360


def add_image_page(doc: pymupdf.Document, stream: bytes, rotate_cw: int, display_w: int, display_h: int,
                   size_mode: str = "original") -> tuple[pymupdf.Page, pymupdf.Rect]:
    pw, ph, rect = page_size_for(display_w, display_h, size_mode)
    page = doc.new_page(width=pw, height=ph)
    # PyMuPDF's `rotate` is counter-clockwise; ours is clockwise.
    page.insert_image(rect, stream=stream, rotate=(360 - rotate_cw) % 360, keep_proportion=False)
    return page, rect


def add_pdf_page(doc: pymupdf.Document, src: pymupdf.Document, index: int, user_rotation: int
                 ) -> tuple[pymupdf.Page, pymupdf.Rect]:
    doc.insert_pdf(src, from_page=index, to_page=index)
    page = doc[-1]
    if user_rotation:
        page.set_rotation((page.rotation + user_rotation) % 360)
    return page, page.rect  # page.rect = displayed (rotated) rect


def existing_text_rects(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    """Display-space rectangles (and words) of text already present on the page."""
    out = []
    mat = page.rotation_matrix
    for w in page.get_text("words"):
        r = pymupdf.Rect(w[:4])
        if page.rotation:
            r = r * mat
        out.append((r, w[4]))
    return out
