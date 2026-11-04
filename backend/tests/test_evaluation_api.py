"""/api/evaluations input handling: images and single PDF pages are accepted, everything
else is refused with a user-facing message. OCR itself is stubbed (not an accuracy test)."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image


def _pdf(pages: int = 3) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        pg = doc.new_page(width=300, height=200)
        pg.insert_text((40, 100), f"page {i + 1}", fontsize=24)
    return doc.tobytes()


def _jpeg() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(b, "JPEG")
    return b.getvalue()


@pytest.fixture()
def stub_eval(monkeypatch):
    """Capture what the endpoint hands to the evaluator instead of running OCR."""
    calls: list[dict] = []
    import app.evaluation.runner as runner

    def fake(data, ground_truth, engines, owner_id=None, models=None):
        calls.append({"data": data, "engines": engines, "models": models})
        return {"ground_truth_provided": bool(ground_truth), "note": None, "results": []}

    monkeypatch.setattr(runner, "evaluate_image", fake)
    return calls


def test_pdf_page_is_rendered_and_evaluated(client, stub_eval):
    r = client.post("/api/evaluations", files={"image": ("notes.pdf", _pdf(3), "application/pdf")},
                    data={"engines": "local", "page": "2"})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == {"kind": "pdf", "page": 2, "pages": 3}
    assert len(stub_eval) == 1
    img = Image.open(io.BytesIO(stub_eval[0]["data"]))
    assert img.format == "JPEG" and img.width > img.height  # the rendered landscape page


def test_pdf_defaults_to_first_page(client, stub_eval):
    r = client.post("/api/evaluations", files={"image": ("n.pdf", _pdf(2), "application/pdf")}, data={"engines": "local"})
    assert r.status_code == 200, r.text
    assert r.json()["source"]["page"] == 1


def test_pdf_page_out_of_range_is_400(client, stub_eval):
    r = client.post("/api/evaluations", files={"image": ("n.pdf", _pdf(2), "application/pdf")},
                    data={"engines": "local", "page": "5"})
    assert r.status_code == 400 and "2쪽" in r.json()["detail"]
    assert stub_eval == []


def test_broken_pdf_is_400(client, stub_eval):
    r = client.post("/api/evaluations", files={"image": ("x.pdf", b"%PDF-1.7\n garbage", "application/pdf")},
                    data={"engines": "local"})
    assert r.status_code == 400 and "PDF" in r.json()["detail"]


def test_image_still_accepted_and_unknown_type_refused(client, stub_eval):
    r = client.post("/api/evaluations", files={"image": ("a.jpg", _jpeg(), "image/jpeg")}, data={"engines": "local"})
    assert r.status_code == 200, r.text
    assert r.json()["source"] == {"kind": "image", "page": 1, "pages": 1}
    r = client.post("/api/evaluations", files={"image": ("a.txt", b"hello", "text/plain")}, data={"engines": "local"})
    assert r.status_code == 400 and "PDF" in r.json()["detail"]
