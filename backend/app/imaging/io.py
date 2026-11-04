"""Safe image loading and page rendering (display orientation)."""

from __future__ import annotations

import io

import numpy as np
import pymupdf
from PIL import Image, ImageOps

from app.core.config import get_settings

# Decompression-bomb guard (PIL raises DecompressionBombError above 2x this).
Image.MAX_IMAGE_PIXELS = get_settings().max_image_pixels

EXIF_ORIENTATION = 0x0112
# EXIF orientation -> (clockwise rotation, mirrored)
_EXIF_MAP = {1: (0, False), 2: (0, True), 3: (180, False), 4: (180, True),
             5: (90, True), 6: (90, False), 7: (270, True), 8: (270, False)}


def exif_info(img: Image.Image) -> tuple[int, bool]:
    try:
        o = img.getexif().get(EXIF_ORIENTATION, 1)
    except Exception:
        o = 1
    return _EXIF_MAP.get(int(o) if o else 1, (0, False))


def to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def open_image_bytes(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def load_oriented(data: bytes) -> Image.Image:
    """Decode, apply EXIF orientation, convert to RGB."""
    img = open_image_bytes(data)
    img = ImageOps.exif_transpose(img)
    return to_rgb(img)


def rotate_cw(img: Image.Image, degrees: int) -> Image.Image:
    d = degrees % 360
    if d == 90:
        return img.transpose(Image.Transpose.ROTATE_270)
    if d == 180:
        return img.transpose(Image.Transpose.ROTATE_180)
    if d == 270:
        return img.transpose(Image.Transpose.ROTATE_90)
    return img


def render_pdf_page(pdf_bytes: bytes, index: int, dpi: int) -> Image.Image:
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[index]
        pix = page.get_pixmap(dpi=dpi, alpha=False, colorspace=pymupdf.csRGB)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def page_display_image(page, source_bytes: bytes, dpi: int | None = None) -> Image.Image:
    """Image of a Page exactly as displayed (EXIF + user rotation applied)."""
    if page.source.kind == "pdf":
        img = render_pdf_page(source_bytes, page.source_page_index, dpi or get_settings().pdf_render_dpi)
    else:
        img = load_oriented(source_bytes)
    return rotate_cw(img, page.user_rotation)


def pil_to_bgr(img: Image.Image) -> np.ndarray:
    a = np.asarray(to_rgb(img))
    return a[:, :, ::-1].copy()


def bgr_to_pil(a: np.ndarray) -> Image.Image:
    return Image.fromarray(a[:, :, ::-1].copy())


def encode_jpeg(img: Image.Image, quality: int = 90) -> bytes:
    b = io.BytesIO()
    to_rgb(img).save(b, "JPEG", quality=quality, optimize=True)
    return b.getvalue()


def encode_png(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG", optimize=False)
    return b.getvalue()


def make_thumbnail(img: Image.Image, max_side: int = 360) -> bytes:
    t = img.copy()
    t.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return encode_jpeg(t, 82)
