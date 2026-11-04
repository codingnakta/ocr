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


HEIGHT_FILL = 0.85  # text height relative to the detected line box (boxes include padding)


def _resolve_overlaps(quads: list[list[list[float]]]) -> list[list[list[float]]]:
    """Split horizontal overlaps between boxes on the same row at the midpoint.

    Detector boxes are padded, so neighbouring boxes on one row often overlap (e.g.
    "비동기식 :" and "입력이 바뀌는 ..."). Overlapping text runs make drag-selection in
    viewers ambiguous at the border, so each run is trimmed to its own half."""
    out = [[list(p) for p in q] for q in quads]
    boxes = [[min(p[0] for p in q), min(p[1] for p in q), max(p[0] for p in q), max(p[1] for p in q)] for q in out]
    order = sorted(range(len(out)), key=lambda i: boxes[i][0])
    for a_i, i in enumerate(order):
        for j in order[a_i + 1:]:
            bi, bj = boxes[i], boxes[j]
            if bj[0] >= bi[2]:
                continue
            vo = min(bi[3], bj[3]) - max(bi[1], bj[1])
            if vo < 0.5 * min(bi[3] - bi[1], bj[3] - bj[1]):
                continue  # different rows
            if abs(quad_metrics(out[i])["angle"]) > 20 or abs(quad_metrics(out[j])["angle"]) > 20:
                continue
            mid = (bi[2] + bj[0]) / 2
            if mid <= bi[0] + 1 or mid >= bj[2] - 1:
                continue  # one box contains the other: leave it
            for k in (1, 2):  # right edge of the left box
                out[i][k][0] = min(out[i][k][0], mid)
            for k in (0, 3):  # left edge of the right box
                out[j][k][0] = max(out[j][k][0], mid)
            boxes[i][2] = min(bi[2], mid)
            boxes[j][0] = max(bj[0], mid)
    return out


def place_items(page: pymupdf.Page, items: list[LayerItem]) -> list[dict]:
    """Write invisible text for each item. Returns placement info for verification."""
    font = layer_font()
    span = font.ascender - font.descender  # in units of font size
    derot = page.derotation_matrix if page.rotation else None
    placed = []
    valid = [it for it in items if _clean(it.text) and it.quad]
    trimmed = _resolve_overlaps([it.quad for it in valid])
    for it, tq in zip(valid, trimmed):
        text = _clean(it.text)
        pts = [pymupdf.Point(x, y) for x, y in tq]
        if derot is not None:
            pts = [p * derot for p in pts]
        q = [[p.x, p.y] for p in pts]
        m = quad_metrics(q)
        if m["height"] < 1 or m["length"] < 1:
            continue
        fs = max(1.0, HEIGHT_FILL * m["height"] / span)
        ang = math.radians(m["angle"])
        ux, uy = math.cos(ang), math.sin(ang)  # baseline direction
        nx, ny = uy, -ux  # "up" normal in a y-down space
        # left-edge midpoint of the box; centre the text run's em box on it
        lm = pymupdf.Point((pts[0].x + pts[3].x) / 2, (pts[0].y + pts[3].y) / 2)
        c = (font.ascender + font.descender) / 2 * fs
        origin = pymupdf.Point(lm.x - nx * c, lm.y - ny * c)
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
