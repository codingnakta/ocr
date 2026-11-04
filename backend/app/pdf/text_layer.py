"""Invisible (render mode 3) text layer placed over page images.

Font: PyMuPDF's built-in "Droid Sans Fallback" (Apache-2.0), which covers Hangul,
Latin, digits and CJK. It is embedded as a Type0/Identity-H subset with a ToUnicode
map, so copy/paste and search work without any font installed on the viewer's system.

Each OCR line is written as one text run whose font size matches the line height and
which is horizontally scaled (and rotated) to span exactly the detected box. Viewers
therefore highlight the handwriting itself when the user drags over it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import pymupdf

from app.imaging.geometry import quad_metrics


@lru_cache(maxsize=1)
def layer_font() -> pymupdf.Font:
    return pymupdf.Font("cjk")


@dataclass
class LayerItem:
    text: str
    quad: list[list[float]]  # display-space points (PDF points, top-left origin)


def _clean(text: str) -> str:
    t = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    # drop control characters; keep everything printable
    return "".join(ch for ch in t if ch.isprintable())


def place_items(page: pymupdf.Page, items: list[LayerItem]) -> list[dict]:
    """Write invisible text for each item. Returns placement info for verification."""
    font = layer_font()
    span = font.ascender - font.descender  # in units of font size
    derot = page.derotation_matrix if page.rotation else None
    placed = []
    for it in items:
        text = _clean(it.text)
        if not text or not it.quad:
            continue
        pts = [pymupdf.Point(x, y) for x, y in it.quad]
        if derot is not None:
            pts = [p * derot for p in pts]
        q = [[p.x, p.y] for p in pts]
        m = quad_metrics(q)
        if m["height"] < 1 or m["length"] < 1:
            continue
        fs = max(1.0, m["height"] / span)
        ang = math.radians(m["angle"])
        ux, uy = math.cos(ang), math.sin(ang)  # baseline direction
        nx, ny = uy, -ux  # "up" normal in a y-down space
        bl = pts[3]
        origin = pymupdf.Point(bl.x + nx * (-font.descender) * fs, bl.y + ny * (-font.descender) * fs)
        natural = font.text_length(text, fontsize=fs)
        if natural <= 0:
            continue
        sx = max(0.05, min(20.0, m["length"] / natural))
        tw = pymupdf.TextWriter(page.rect)
        # Trailing space: keeps adjacent runs from being merged into one word by
        # extractors/viewers when two boxes touch (e.g. "미토콘드리아" + "=").
        tw.append(origin, text + " ", font=font, fontsize=fs)
        morph = (origin, pymupdf.Matrix(sx, 0, 0, 1, 0, 0) * pymupdf.Matrix(-m["angle"]))
        tw.write_text(page, render_mode=3, morph=morph)
        placed.append({"text": text, "quad": it.quad})
    return placed
