"""API end-to-end with a fake Claude (mock). Proves upload -> OCR job -> edit -> export
-> download works and that state is persisted; does NOT prove real OCR accuracy."""

from __future__ import annotations

import io

import pymupdf

from conftest import drain
from synth import KO_SAMPLE, SynthLine, fonts_available, render_page

FAMILY = "nanum-pen-script" if fonts_available() else "printed"


def jpeg(lines, seed=1, **kw) -> bytes:
    p = render_page(lines, family=FAMILY, seed=seed, **kw)
    b = io.BytesIO()
    p.image.save(b, "JPEG", quality=90)
    return b.getvalue()


def upload(client, files, doc_id=None):
    payload = [("files", (name, data, "image/jpeg")) for name, data in files]
    if doc_id:
        r = client.post(f"/api/documents/{doc_id}/files", files=payload)
    else:
        r = client.post("/api/documents", files=payload, data={"title": "테스트 문서"})
    assert r.status_code in (200, 201), r.text
    return r.json()


def test_full_flow_upload_ocr_edit_export(client, fake_claude):
    small = dict(width=1000, height=1300, font_px=50)
    doc = upload(client, [("a.jpg", jpeg(KO_SAMPLE[:3], 1, **small)), ("b.jpg", jpeg(KO_SAMPLE[3:], 2, **small))])
    doc_id = doc["id"]
    assert doc["page_count"] == 2 and doc["upload"]["errors"] == []

    # reorder + rotate
    ids = [p["id"] for p in doc["pages"]]
    r = client.put(f"/api/documents/{doc_id}/pages/order", json={"page_ids": ids[::-1]})
    assert [p["id"] for p in r.json()["pages"]] == ids[::-1]
    r = client.patch(f"/api/documents/{doc_id}/pages/{ids[0]}", json={"rotate": 90})
    assert r.json()["rotation"] == 90
    client.patch(f"/api/documents/{doc_id}/pages/{ids[0]}", json={"rotate": -90})

    est = client.post(f"/api/documents/{doc_id}/ocr/estimate", json={"mode": "hybrid"}).json()
    assert est["pages"] == 2 and est["estimated_cost_usd"] > 0 and est["is_estimate"] is True

    job = client.post(f"/api/documents/{doc_id}/ocr", json={"mode": "ai", "cost_limit_usd": 2}).json()
    assert job["status"] == "queued" and job["total_pages"] == 2
    assert drain() == 2
    job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "completed" and job["completed_pages"] == 2
    assert job["api"]["calls"] == 2 and job["api"]["cost_usd"] > 0
    assert job["api"]["input_tokens"] == 12000

    # page-number addressing (spec) and id addressing both work
    ocr = client.get(f"/api/documents/{doc_id}/pages/1/ocr").json()
    assert ocr["regions"], "no regions"
    reg = ocr["regions"][0]
    r = client.patch(f"/api/documents/{doc_id}/pages/1/ocr",
                     json={"regions": [{"id": reg["id"], "text_user": "사용자가 고친 문장", "reviewed": True}]})
    edited = next(x for x in r.json()["regions"] if x["id"] == reg["id"])
    assert edited["text"] == "사용자가 고친 문장" and edited["text_ocr"] == reg["text_ocr"]  # original kept

    # export pdf + txt + md + json and download
    for fmt in ("pdf", "txt", "md", "json"):
        ex = client.post(f"/api/documents/{doc_id}/export", json={"format": fmt}).json()
        drain()
        ex = client.get(f"/api/exports/{ex['id']}").json()
        assert ex["status"] == "done", ex
        d = client.get(ex["download_url"])
        assert d.status_code == 200
        if fmt == "pdf":
            pdf = pymupdf.open(stream=d.content)
            assert pdf.page_count == 2
            assert "사용자가고친문장" in "".join(pdf[0].get_text().split())  # edit reflected (test 9)
            assert ex["report"]["position_ok_ratio"] == 1.0
        else:
            assert "사용자가 고친 문장" in d.content.decode("utf-8")
    latest = client.get(f"/api/documents/{doc_id}/download?format=pdf")
    assert latest.status_code == 200 and latest.headers["content-type"] == "application/pdf"


def test_rerun_preserves_user_edits(client, fake_claude):
    doc = upload(client, [("a.jpg", jpeg(KO_SAMPLE[:2], 3, width=1000, height=1000, font_px=50))])
    doc_id = doc["id"]
    client.post(f"/api/documents/{doc_id}/ocr", json={"mode": "ai"})
    drain()
    reg = client.get(f"/api/documents/{doc_id}/pages/1/ocr").json()["regions"][0]
    client.patch(f"/api/documents/{doc_id}/pages/1/ocr", json={"regions": [{"id": reg["id"], "text_user": "내 수정"}]})
    fake_claude.texts = [["AI가 바꾼 다른 텍스트", "두번째"]]
    r = client.post(f"/api/documents/{doc_id}/ocr", json={"mode": "ai", "rerun": True, "use_cache": False})
    assert r.status_code == 202, r.text
    drain()
    regs = client.get(f"/api/documents/{doc_id}/pages/1/ocr").json()["regions"]
    first = regs[0]
    assert first["text"] == "내 수정" and first["text_ocr"] == "AI가 바꾼 다른 텍스트"


def test_failed_page_does_not_lose_others_and_can_retry(client, fake_claude):
    """Test 10: an API failure on one page keeps the other pages' results."""
    files = [(f"p{i}.jpg", jpeg([SynthLine(f"페이지 {i}")], i, width=700, height=900, font_px=48)) for i in range(4)]
    doc = upload(client, files)
    fake_claude.fail_pages = {2}
    job = client.post(f"/api/documents/{doc['id']}/ocr", json={"mode": "ai"}).json()
    drain()
    st = client.get(f"/api/jobs/{job['id']}").json()
    assert st["status"] == "completed_with_errors"
    assert st["completed_pages"] == 3 and st["failed_pages"] == 1
    assert st["api"]["failed"] == 1
    pages = client.get(f"/api/documents/{doc['id']}").json()["pages"]
    assert sum(p["ocr_status"] == "done" for p in pages) == 3
    fake_claude.fail_pages = set()
    r = client.post(f"/api/jobs/{job['id']}/retry-failed")
    assert r.status_code == 200
    drain()
    st = client.get(f"/api/jobs/{job['id']}").json()
    assert st["status"] == "completed" and st["completed_pages"] == 4
    assert fake_claude.page_calls == 5  # 4 pages + 1 retry, no duplicate calls for finished pages


def test_cost_limit_blocks_requests(client, fake_claude):
    """Test 12: requests beyond the cost limit are blocked before being sent."""
    files = [(f"p{i}.jpg", jpeg([SynthLine(f"쪽 {i}")], i, width=600, height=800, font_px=44)) for i in range(5)]
    doc = upload(client, files)
    # each fake call costs 6000*4/1e6 + 500*20/1e6 = $0.034
    job = client.post(f"/api/documents/{doc['id']}/ocr", json={"mode": "ai", "cost_limit_usd": 0.08}).json()
    drain()
    st = client.get(f"/api/jobs/{job['id']}").json()
    assert st["status"] == "paused" and st["stop_reason"] == "cost_limit"
    assert fake_claude.page_calls == 2 and st["api"]["cost_usd"] <= 0.08
    assert st["completed_pages"] == 2 and st["pending_pages"] == 3
    # raise the limit and resume: continues from where it stopped
    r = client.post(f"/api/jobs/{job['id']}/resume", json={"cost_limit_usd": 1.0})
    assert r.status_code == 200
    drain()
    st = client.get(f"/api/jobs/{job['id']}").json()
    assert st["status"] == "completed" and fake_claude.page_calls == 5


def test_daily_cost_limit(client, fake_claude, monkeypatch):
    from app.core import config

    monkeypatch.setenv("DAILY_COST_LIMIT_USD", "0.05")
    config.get_settings.cache_clear()
    files = [(f"p{i}.jpg", jpeg([SynthLine(f"쪽 {i}")], i, width=600, height=800, font_px=44)) for i in range(3)]
    doc = upload(client, files)
    job = client.post(f"/api/documents/{doc['id']}/ocr", json={"mode": "ai", "cost_limit_usd": 10}).json()
    drain()
    st = client.get(f"/api/jobs/{job['id']}").json()
    assert st["stop_reason"] == "cost_limit" and fake_claude.page_calls == 1
    assert "일일" in st["error"]


def test_pause_resume_cancel(client, fake_claude):
    files = [(f"p{i}.jpg", jpeg([SynthLine(f"쪽 {i}")], i, width=600, height=800, font_px=44)) for i in range(6)]
    doc = upload(client, files)
    job = client.post(f"/api/documents/{doc['id']}/ocr", json={"mode": "ai"}).json()
    drain(stop_after=2)
    assert client.post(f"/api/jobs/{job['id']}/pause").json()["status"] == "paused"
    assert drain() == 0  # paused jobs are not processed
    st = client.post(f"/api/jobs/{job['id']}/resume").json()
    assert st["status"] == "queued"
    drain(stop_after=2)
    st = client.post(f"/api/jobs/{job['id']}/cancel").json()
    assert st["status"] == "cancelled" and st["cancelled_pages"] == 2 and st["completed_pages"] == 4
    assert fake_claude.page_calls == 4
    # a new job only takes the pages that are not done yet
    job2 = client.post(f"/api/documents/{doc['id']}/ocr", json={"mode": "ai"}).json()
    assert job2["total_pages"] == 2


def test_local_mode_costs_nothing(client):
    doc = upload(client, [("a.jpg", jpeg([SynthLine("Hello local OCR")], 1, width=900, height=600, font_px=60))])
    est = client.post(f"/api/documents/{doc['id']}/ocr/estimate", json={"mode": "local"}).json()
    assert est["estimated_cost_usd"] == 0
    job = client.post(f"/api/documents/{doc['id']}/ocr", json={"mode": "local"}).json()
    drain()
    st = client.get(f"/api/jobs/{job['id']}").json()
    assert st["status"] == "completed" and st["api"]["calls"] == 0
    regs = client.get(f"/api/documents/{doc['id']}/pages/1/ocr").json()["regions"]
    assert any("Hello" in r["text"] for r in regs)


def test_cache_avoids_duplicate_api_calls(client, fake_claude):
    data = jpeg([SynthLine("같은 이미지")], 9, width=700, height=700, font_px=50)
    d1 = upload(client, [("a.jpg", data)])
    client.post(f"/api/documents/{d1['id']}/ocr", json={"mode": "ai"})
    drain()
    d2 = upload(client, [("same.jpg", data)])
    job = client.post(f"/api/documents/{d2['id']}/ocr", json={"mode": "ai"}).json()
    drain()
    st = client.get(f"/api/jobs/{job['id']}").json()
    assert st["from_cache_pages"] == 1 and st["api"]["calls"] == 0
    assert fake_claude.page_calls == 1


def test_manual_region_and_reocr_local(client):
    doc = upload(client, [("a.jpg", jpeg([SynthLine("Manual region test")], 1, width=900, height=600, font_px=60))])
    r = client.patch(f"/api/documents/{doc['id']}/pages/1/ocr",
                     json={"create": [{"polygon": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.2], [0.1, 0.2]],
                                       "text_user": "직접 추가한 텍스트"}]})
    assert r.json()["regions"][0]["text"] == "직접 추가한 텍스트"
    r = client.post(f"/api/documents/{doc['id']}/pages/1/reocr", json={"rect": [0.0, 0.0, 1.0, 0.4], "engine": "local"})
    assert r.status_code == 200 and "Manual" in r.json()["text"]


def test_delete_document_removes_files(client, env):
    doc = upload(client, [("a.jpg", jpeg([SynthLine("삭제")], 1, width=600, height=600, font_px=50))])
    files_dir = env / "files" / "docs" / doc["id"]
    assert files_dir.exists()
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert not files_dir.exists()
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404
