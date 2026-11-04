"""Synthetic test page generator.

Produces page images with known ground-truth text *and* known line positions, so the
PDF text layer alignment can be measured objectively.

IMPORTANT: images rendered with handwriting-style fonts are NOT real handwriting.
They are only useful for pipeline/regression tests (positions, PDF layer, colors,
perspective), not for judging handwriting recognition accuracy.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

INK = {
    "black": (25, 25, 30),
    "blue": (20, 50, 170),
    "red": (190, 25, 30),
    "orange": (230, 120, 10),
    "green": (20, 120, 50),
}
HIGHLIGHT = {"yellow": (255, 240, 90), "pink": (255, 150, 200)}


@dataclass
class SynthLine:
    text: str
    color: str = "black"
    highlight: str | None = None


@dataclass
class SynthPage:
    image: Image.Image
    lines: list[str]
    # quad per line in final image pixel coords [[x,y]*4] (tl,tr,br,bl)
    quads: list[list[list[float]]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def jpeg_bytes(self, quality: int = 92) -> bytes:
        b = io.BytesIO()
        self.image.save(b, "JPEG", quality=quality)
        return b.getvalue()

    def png_bytes(self) -> bytes:
        b = io.BytesIO()
        self.image.save(b, "PNG")
        return b.getvalue()


class _FontSet:
    """A family split into unicode-range subset files (fontsource). Picks, per
    character, the first subset whose cmap contains it."""

    def __init__(self, files: list[Path], size: int):
        from fontTools.ttLib import TTFont

        self.fonts: list[tuple[set[int], ImageFont.FreeTypeFont]] = []
        for f in files:
            cmap = set(TTFont(str(f)).getBestCmap().keys())
            self.fonts.append((cmap, ImageFont.truetype(str(f), size)))

    def font_for(self, ch: str) -> ImageFont.FreeTypeFont:
        cp = ord(ch)
        for cmap, font in self.fonts:
            if cp in cmap:
                return font
        # glyph missing from the handwriting family (e.g. arrows): use printed CJK font
        size = self.fonts[0][1].size
        return ImageFont.truetype(_printed_font_path(), size)


@lru_cache(maxsize=16)
def _printed_font_path() -> str:
    import pymupdf

    p = Path(__file__).resolve().parent / "assets" / "_cjk_printed.ttf"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(pymupdf.Font("cjk").buffer)
    return str(p)


@lru_cache(maxsize=16)
def get_fontset(family: str, size: int) -> _FontSet:
    if family == "printed":
        fs = _FontSet.__new__(_FontSet)
        f = ImageFont.truetype(_printed_font_path(), size)
        fs.fonts = [(set(range(0x110000)), f)]
        return fs
    files = sorted((FONT_DIR / family).glob("*-400-normal.woff"))
    if not files:
        raise FileNotFoundError(f"font family {family} missing; run scripts/fetch_assets.py fonts")
    return _FontSet(files, size)


def fonts_available() -> bool:
    return any(FONT_DIR.glob("*/*-400-normal.woff"))


def render_page(
    lines: list[SynthLine | str],
    family: str = "nanum-pen-script",
    width: int = 1700,
    height: int = 2200,
    font_px: int = 72,
    margin: int = 120,
    line_gap: float = 1.9,
    seed: int = 1,
    wobble: float = 1.0,
    paper: bool = True,
    perspective: float = 0.0,
    shading: float = 0.0,
    rotate_deg: float = 0.0,
    noise: float = 0.0,
) -> SynthPage:
    rnd = random.Random(seed)
    lines = [ln if isinstance(ln, SynthLine) else SynthLine(ln) for ln in lines]
    bg = (248, 246, 238) if paper else (255, 255, 255)
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    if paper:  # ruled notebook lines
        for y in range(margin + int(font_px * 1.2), height - margin, int(font_px * line_gap)):
            draw.line([(margin // 2, y), (width - margin // 2, y)], fill=(200, 215, 235), width=2)
    fs = get_fontset(family, font_px)
    quads: list[list[list[float]]] = []
    y = margin
    for ln in lines:
        x = margin + rnd.randint(-10, 10) * wobble
        color = INK[ln.color]
        min_x, max_x, min_y, max_y = 1e9, -1e9, 1e9, -1e9
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ldraw = ImageDraw.Draw(layer)
        for ch in ln.text:
            font = fs.font_for(ch)
            dy = rnd.uniform(-3, 3) * wobble
            if ch == " ":
                x += font_px * 0.35
                continue
            bbox = ldraw.textbbox((x, y + dy), ch, font=font)
            ldraw.text((x, y + dy), ch, font=font, fill=color + (255,))
            min_x, max_x = min(min_x, bbox[0]), max(max_x, bbox[2])
            min_y, max_y = min(min_y, bbox[1]), max(max_y, bbox[3])
            x = bbox[2] + rnd.uniform(1, 5) * wobble
        if ln.highlight and max_x > min_x:
            hl = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(hl).rectangle(
                [min_x - 6, min_y + (max_y - min_y) * 0.25, max_x + 6, max_y + 4],
                fill=HIGHLIGHT[ln.highlight] + (140,),
            )
            img.paste(Image.alpha_composite(img.convert("RGBA"), hl).convert("RGB"))
        img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"))
        quads.append([[min_x, min_y], [max_x, min_y], [max_x, max_y], [min_x, max_y]])
        y += int(font_px * line_gap)

    arr = np.asarray(img).astype(np.float32)
    pts = np.array([q for quad in quads for q in quad], dtype=np.float32).reshape(-1, 1, 2)

    import cv2

    h, w = arr.shape[:2]
    M = np.eye(3, dtype=np.float64)
    if rotate_deg:
        R = cv2.getRotationMatrix2D((w / 2, h / 2), rotate_deg, 1.0)
        M = np.vstack([R, [0, 0, 1]]) @ M
    if perspective:
        d = perspective * min(w, h)
        src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        dst = np.float32([[d, d * 0.6], [w - d * 0.4, 0], [w, h], [d * 0.2, h - d * 0.3]])
        P = cv2.getPerspectiveTransform(src, dst)
        M = P @ M
    if rotate_deg or perspective:
        # put the "paper" on a darker desk background, like a phone photo
        canvas_bg = (90, 80, 70)
        arr = cv2.warpPerspective(arr, M, (w, h), borderValue=canvas_bg)
        pts = cv2.perspectiveTransform(pts, M)
    if shading:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        g = 1.0 - shading * (0.6 * xx / w + 0.4 * yy / h)
        arr = arr * g[..., None]
    if noise:
        arr = arr + np.random.default_rng(seed).normal(0, noise, arr.shape)
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if noise:
        out = out.filter(ImageFilter.GaussianBlur(0.6))
    q = pts.reshape(-1, 4, 2).tolist()
    return SynthPage(image=out, lines=[ln.text for ln in lines], quads=q)


KO_SAMPLE = [
    SynthLine("오늘의 수업 정리: 광합성", "black"),
    SynthLine("빛 에너지를 화학 에너지로 바꾼다", "blue"),
    SynthLine("중요! 엽록체에서 일어남", "red", highlight="yellow"),
    SynthLine("6CO2 + 6H2O → C6H12O6 + 6O2", "black"),
    SynthLine("시험 범위는 3장부터 5장까지", "orange"),
]
EN_SAMPLE = [
    SynthLine("Lecture notes: Photosynthesis", "black"),
    SynthLine("Light energy becomes chemical energy", "blue"),
    SynthLine("Important: happens in chloroplasts", "red", highlight="pink"),
    SynthLine("Exam covers chapters 3 to 5", "black"),
]
MIXED_SAMPLE = [
    SynthLine("Chapter 2 세포의 구조 (Cell structure)", "black"),
    SynthLine("Mitochondria 미토콘드리아 = ATP 생산", "blue"),
    SynthLine("DNA는 nucleus 안에 있다", "red"),
    SynthLine("Q. Ribosome의 역할은?", "black"),
]
