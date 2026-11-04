"""Preprocessing (color preservation, geometric mapping) and metric functions."""

from __future__ import annotations

import numpy as np
import pytest

from app.evaluation.metrics import cer, missed_lines, similarity, wer
from app.imaging.geometry import quad_bbox, iou_boxes
from app.imaging.io import pil_to_bgr
from app.imaging.preprocess import hue_stats, preprocess
from synth import INK, KO_SAMPLE, SynthLine, fonts_available, render_page

FAMILY = "nanum-pen-script" if fonts_available() else "printed"


def test_cer_wer():
    assert cer("가나다", "가나다") == 0
    assert cer("가나다라", "가나라") == pytest.approx(0.25)
    assert cer("a b", "ab") == 0  # spaces ignored by default
    assert cer("a b", "ab", ignore_space=False) == pytest.approx(1 / 3)
    assert wer("오늘 수업 정리", "오늘 수업 정래") == pytest.approx(1 / 3)
    assert similarity("동기식", "동기식") == 1.0
    assert missed_lines("광합성 정리\n엽록체에서 일어남\n시험 범위", "광합성 정리\n시험 범위") == ["엽록체에서 일어남"]


@pytest.mark.parametrize("ink", ["blue", "red", "orange"])
def test_colored_ink_hue_preserved(ink):
    lines = [SynthLine("색깔 있는 필기 테스트", ink), SynthLine("Colored handwriting", ink)]
    p = render_page(lines, family=FAMILY, seed=1, shading=0.35, noise=3)
    src = pil_to_bgr(p.image)
    out = preprocess(src, {"document": False, "resize": False}).image
    target = np.array(INK[ink][::-1], dtype=np.int16)  # BGR
    mask = np.abs(src.astype(np.int16) - target).sum(axis=2) < 120
    assert mask.sum() > 500
    h0, s0 = hue_stats(src, mask)
    h1, s1 = hue_stats(out, mask)
    dh = min(abs(h0 - h1), 360 - abs(h0 - h1))
    assert dh < 15, (h0, h1)
    assert s1 > 0.6 * s0  # ink not washed out to gray


def test_highlighter_kept():
    p = render_page([SynthLine("형광펜 표시", "black", highlight="yellow")], family=FAMILY, seed=2)
    src = pil_to_bgr(p.image)
    out = preprocess(src, {"document": False, "resize": False}).image
    hl = np.array((90, 240, 255), dtype=np.int16)  # approx BGR of blended yellow
    mask = np.abs(src.astype(np.int16) - hl).sum(axis=2) < 90
    assert mask.sum() > 300
    _, s1 = hue_stats(out, mask)
    assert s1 > 60  # still saturated (yellow), not bleached to white


def test_perspective_photo_regions_map_back_onto_original():
    """Local detection on the corrected image, mapped back, must land on the true lines."""
    from app.ocr.claude_engine import NullLedger
    from app.ocr.pipeline import run_page_ocr

    p = render_page(KO_SAMPLE, family=FAMILY, seed=3, perspective=0.06, shading=0.3, noise=3)
    out = run_page_ocr(pil_to_bgr(p.image), "local", NullLedger())
    w, h = p.image.size
    found = [quad_bbox([[x * w, y * h] for x, y in r.quad]) for r in out.result.regions if r.quad]
    assert any(s["step"] == "document" for s in out.steps)
    for gt in p.quads:
        g = quad_bbox(gt)
        # best overlap of the ground-truth line with any detected region (lines may be split)
        cover = min(1.0, sum(_covered(g, f) for f in found))
        assert cover > 0.5, (gt, cover)


def _covered(g, f):
    ix = max(0.0, min(g[2], f[2]) - max(g[0], f[0]))
    iy = max(0.0, min(g[3], f[3]) - max(g[1], f[1]))
    return ix * iy / ((g[2] - g[0]) * (g[3] - g[1]))


def test_rotated_photo_auto_orientation():
    from app.ocr.claude_engine import NullLedger
    from app.ocr.pipeline import run_page_ocr

    lines = [SynthLine("Upside down page detection works"), SynthLine("Second line of printed text"),
             SynthLine("Third line for voting"), SynthLine("Fourth line here")]
    p = render_page(lines, family="printed", seed=1, wobble=0, paper=False, font_px=56)
    upside = p.image.rotate(180)
    out = run_page_ocr(pil_to_bgr(upside), "local", NullLedger())
    assert any(s["step"] == "orientation" for s in out.steps)
    text = " ".join(r.text for r in out.result.regions)
    assert "Upside" in text and "Fourth" in text
