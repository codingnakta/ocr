"""Searchable PDF: Unicode extraction, positions, original image preservation.

These verify the *file*. Drag-selection in viewers is tested separately
(e2e/pdfjs.spec.ts with PDF.js, plus the manual viewer checklist in docs/testing.md).
"""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

from app.pdf.exporter import ExportPage, ExportRegion, build_pdf, build_txt
from app.pdf.merger import prepare_image_stream
from synth import EN_SAMPLE, KO_SAMPLE, MIXED_SAMPLE, SynthLine, fonts_available, render_page

FAMILY = "nanum-pen-script" if fonts_available() else "printed"


def page_from_synth(p, n=1, fmt="JPEG", exif_orientation=None):
    buf = io.BytesIO()
    img = p.image
    kw = {}
    if exif_orientation:
        # store the pixels rotated "as the camera saw them" + an EXIF tag that undoes it
        inv = {6: Image.Transpose.ROTATE_90, 8: Image.Transpose.ROTATE_270, 3: Image.Transpose.ROTATE_180}
        img = img.transpose(inv[exif_orientation])
        ex = Image.Exif()
        ex[0x0112] = exif_orientation
        kw["exif"] = ex.tobytes()
    img.save(buf, fmt, **({"quality": 92} if fmt == "JPEG" else {}), **kw)
    w, h = p.image.size
    regs = [ExportRegion(t, [[x / w, y / h] for x, y in q]) for t, q in zip(p.lines, p.quads)]
    mime = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[fmt]
    return ExportPage(number=n, kind="image", source_bytes=buf.getvalue(), mime=mime, display_w=w, display_h=h,
                      regions=regs)


def extract_all(data: bytes) -> dict:
    import pypdf
    from pdfminer.high_level import extract_text

    out = {"pymupdf": "".join(pg.get_text() for pg in pymupdf.open(stream=data))}
    out["pypdf"] = "".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(data)).pages)
    out["pdfminer"] = extract_text(io.BytesIO(data))
    return out


def squash(s: str) -> str:
    return "".join(s.split())


def test_korean_text_extracts_identically_with_three_extractors():
    p = render_page(KO_SAMPLE, family=FAMILY, seed=2)
    data, rep = build_pdf([page_from_synth(p)])
    assert rep["position_ok_ratio"] == 1.0
    assert rep["extract_similarity_min"] == 1.0
    for name, text in extract_all(data).items():
        for line in p.lines:
            assert squash(line) in squash(text), f"{name} lost: {line}"


def test_search_finds_korean_and_english_at_the_right_place():
    p = render_page(MIXED_SAMPLE, family=FAMILY, seed=4)
    data, _ = build_pdf([page_from_synth(p)])
    page = pymupdf.open(stream=data)[0]
    w = p.image.width
    scale = page.rect.width / w
    for word, line_idx in [("미토콘드리아", 1), ("nucleus", 2), ("Ribosome의", 3)]:
        hits = page.search_for(word)
        assert hits, word
        y0 = min(pt[1] for pt in p.quads[line_idx]) * scale
        y1 = max(pt[1] for pt in p.quads[line_idx]) * scale
        cy = (hits[0].y0 + hits[0].y1) / 2
        assert y0 - 5 <= cy <= y1 + 5, f"{word} found at wrong height"


def test_original_jpeg_embedded_byte_identical():
    p = render_page(EN_SAMPLE, family=FAMILY, seed=1)
    pg = page_from_synth(p)
    data, _ = build_pdf([pg])
    doc = pymupdf.open(stream=data)
    img = doc.extract_image(doc[0].get_images()[0][0])
    assert img["image"] == pg.source_bytes
    assert (img["width"], img["height"]) == p.image.size


@pytest.mark.parametrize("orientation", [6, 8, 3])
def test_exif_rotated_photo_no_recompression_and_text_aligned(orientation):
    p = render_page(KO_SAMPLE[:3], family=FAMILY, seed=3)
    pg = page_from_synth(p, exif_orientation=orientation)
    stream, rot = prepare_image_stream(pg.source_bytes, pg.mime, 0)
    assert stream == pg.source_bytes and rot in (90, 270, 180)
    data, rep = build_pdf([pg])
    assert rep["position_ok_ratio"] == 1.0
    page = pymupdf.open(stream=data)[0]
    assert page.rect.height > page.rect.width  # displayed upright (portrait page)
    # rendered PDF must look like the upright original (not rotated)
    pix = page.get_pixmap(dpi=40)
    rendered = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L").resize((60, 80))
    ref = p.image.convert("L").resize((60, 80))
    import numpy as np

    diff = np.abs(np.asarray(rendered, float) - np.asarray(ref, float)).mean()
    assert diff < 12, diff


def test_webp_and_png_inputs():
    p = render_page(EN_SAMPLE[:2], family=FAMILY, seed=5)
    data, rep = build_pdf([page_from_synth(p, fmt="WEBP"), page_from_synth(p, n=2, fmt="PNG")])
    assert rep["position_ok_ratio"] == 1.0
    assert pymupdf.open(stream=data).page_count == 2


def test_ten_images_into_one_pdf_in_user_order():
    pages = []
    for i in range(10):
        p = render_page([SynthLine(f"페이지 번호 {i + 1} 입니다"), SynthLine(f"Page number {i + 1}")],
                        family=FAMILY, seed=i, width=900, height=1200, font_px=48)
        pages.append(page_from_synth(p, n=i + 1))
    order = [3, 0, 9, 1, 2, 4, 5, 6, 7, 8]
    data, rep = build_pdf([pages[i] for i in order])
    doc = pymupdf.open(stream=data)
    assert doc.page_count == 10
    for out_idx, src_idx in enumerate(order):
        assert f"페이지번호{src_idx + 1}입니다" in squash(doc[out_idx].get_text())
    assert rep["position_ok"] == 20


def test_a4_mode_keeps_text_on_the_image():
    p = render_page(KO_SAMPLE[:2], family=FAMILY, seed=6, width=1600, height=900)  # landscape
    data, rep = build_pdf([page_from_synth(p)], size_mode="a4")
    page = pymupdf.open(stream=data)[0]
    assert round(page.rect.width) == 842 and round(page.rect.height) == 595
    assert rep["position_ok_ratio"] == 1.0


def test_existing_pdf_text_is_not_duplicated_and_scan_gets_layer():
    # page 1: real text PDF, page 2: image-only scan
    src = pymupdf.open()
    pg = src.new_page()
    pg.insert_text((72, 100), "Existing text layer", fontsize=14)
    p = render_page(EN_SAMPLE[:2], family=FAMILY, seed=7, width=800, height=1000, font_px=34, margin=60)
    pg2 = src.new_page(width=595, height=744)
    buf = io.BytesIO()
    p.image.save(buf, "JPEG")
    pg2.insert_image(pg2.rect, stream=buf.getvalue())
    pdf_bytes = src.tobytes()
    # OCR regions for page 1 that duplicate the existing text (must be skipped)
    r_dup = ExportRegion("Existing text layer", [[72 / 595, 88 / 842], [230 / 595, 88 / 842],
                                                  [230 / 595, 104 / 842], [72 / 595, 104 / 842]])
    w, h = p.image.size
    regs2 = [ExportRegion(t, [[x / w, y / h] for x, y in q]) for t, q in zip(p.lines, p.quads)]
    pages = [ExportPage(1, "pdf", pdf_bytes, "application/pdf", pdf_index=0, regions=[r_dup]),
             ExportPage(2, "pdf", pdf_bytes, "application/pdf", pdf_index=1, regions=regs2)]
    data, rep = build_pdf(pages)
    doc = pymupdf.open(stream=data)
    assert doc[0].get_text().count("Existing text layer") == 1
    assert rep["pages"][0]["skipped_existing_text"] == 1
    assert squash(EN_SAMPLE[0].text) in squash(doc[1].get_text())
    assert rep["pages"][1]["position_ok"] == 2


def test_rotated_pdf_page_user_rotation():
    src = pymupdf.open()
    pg = src.new_page(width=600, height=800)
    pdf_bytes = src.tobytes()
    reg = ExportRegion("회전된 페이지 텍스트", [[0.1, 0.1], [0.6, 0.1], [0.6, 0.15], [0.1, 0.15]])
    data, rep = build_pdf([ExportPage(1, "pdf", pdf_bytes, "application/pdf", user_rotation=90, regions=[reg])])
    page = pymupdf.open(stream=data)[0]
    assert page.rotation == 90
    assert rep["position_ok_ratio"] == 1.0
    assert "회전된페이지텍스트" in squash(page.get_text())


def test_unaligned_text_not_placed_but_in_txt():
    p = render_page(KO_SAMPLE[:2], family=FAMILY, seed=8)
    pg = page_from_synth(p)
    pg.regions.append(ExportRegion("위치 모르는 줄", None))
    data, rep = build_pdf([pg])
    assert "위치모르는줄" not in squash(pymupdf.open(stream=data)[0].get_text())
    assert "위치 모르는 줄" in build_txt([pg])


def test_text_layer_is_invisible():
    p = render_page(EN_SAMPLE[:2], family=FAMILY, seed=9)
    pg = page_from_synth(p)
    with_layer, _ = build_pdf([pg])
    pg.regions = []
    without, _ = build_pdf([pg])
    a = pymupdf.open(stream=with_layer)[0].get_pixmap(dpi=50).samples
    b = pymupdf.open(stream=without)[0].get_pixmap(dpi=50).samples
    assert a == b  # the layer adds no visible pixels
