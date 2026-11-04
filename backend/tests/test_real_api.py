"""Tests 1-4 (+ real photos): call the REAL Claude API. Run with RUN_REAL_API=1.

Synthetic pages use handwriting-style *fonts*: they validate the pipeline end to end
with exact ground truth, but they are not real handwriting. Real-photo results are
reported per file in tests/assets/real/.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from app.evaluation.metrics import cer
from app.imaging.io import load_oriented, pil_to_bgr
from app.ocr.claude_engine import NullLedger
from app.ocr.pipeline import run_page_ocr
from app.pdf.exporter import ExportPage, ExportRegion, build_pdf
from synth import EN_SAMPLE, KO_SAMPLE, MIXED_SAMPLE, render_page

pytestmark = pytest.mark.real_api
REAL = Path(__file__).parent / "assets" / "real"


def run(img, mode="hybrid"):
    led = NullLedger()
    out = run_page_ocr(pil_to_bgr(img), mode, led)
    text = "\n".join(r.text for r in out.result.regions if r.text.strip())
    return out, text, led.total_cost


def check_pdf(img, out):
    import io

    b = io.BytesIO()
    img.save(b, "JPEG", quality=90)
    pg = ExportPage(1, "image", b.getvalue(), "image/jpeg", display_w=img.width, display_h=img.height,
                    regions=[ExportRegion(r.text, r.quad) for r in out.result.regions])
    data, rep = build_pdf([pg])
    assert rep["position_ok_ratio"] == 1.0 and rep["extract_similarity_min"] == 1.0
    return pymupdf.open(stream=data)[0].get_text()


@pytest.mark.parametrize("name,sample,family,kw,max_cer", [
    ("test1_korean_handwriting", KO_SAMPLE, "nanum-pen-script", dict(perspective=0.05, shading=0.3, noise=3), 0.05),
    ("test2_english_handwriting", EN_SAMPLE, "gaegu", dict(rotate_deg=2), 0.05),
    ("test3_mixed_handwriting", MIXED_SAMPLE, "gamja-flower", dict(seed=4), 0.05),
    ("test4_printed", MIXED_SAMPLE, "printed", dict(wobble=0, paper=False), 0.02),
])
def test_synthetic_pages(env, name, sample, family, kw, max_cer):
    kw = {"seed": 7, **kw}
    p = render_page(sample, family=family, **kw)
    out, text, cost = run(p.image)
    c = cer(p.text, text)
    print(f"\n{name}: CER={c:.4f} cost=${cost:.4f}\n{text}")
    assert c <= max_cer
    layer = "".join(check_pdf(p.image, out).split())
    assert "".join(p.lines[0].split()) in layer


@pytest.mark.parametrize("photo", sorted(REAL.glob("*.jpg")) or [None])
def test_real_photos(env, photo):
    if photo is None:
        pytest.skip("no real photos in tests/assets/real")
    img = load_oriented(photo.read_bytes())
    out, text, cost = run(img)
    gt_file = photo.with_suffix("").with_suffix(".gt.txt")
    print(f"\n{photo.name}: cost=${cost:.4f}\n{text}")
    check_pdf(img, out)
    if gt_file.exists():
        c = cer(gt_file.read_text(encoding="utf-8"), text)
        print(f"CER vs {gt_file.name}: {c:.4f}")
        assert c <= 0.10
