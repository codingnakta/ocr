"""Test 11: 200 pages, crash after 140, restart, resume from 141 without redoing work."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw
from sqlalchemy import func, select

from conftest import drain


def tiny_jpeg(i: int) -> bytes:
    img = Image.new("RGB", (300, 400), (250, 250, 245))
    ImageDraw.Draw(img).text((20, 40), f"page {i}", fill=(0, 0, 0))
    b = io.BytesIO()
    img.save(b, "JPEG", quality=80)
    return b.getvalue()


def test_200_pages_crash_and_resume(client, fake_claude, monkeypatch):
    from app.core import config

    monkeypatch.setenv("LOCAL_OCR_ENABLED", "false")  # queue mechanics only; keeps the test fast
    config.get_settings.cache_clear()

    r = client.post("/api/documents", json={"title": "200쪽"})
    doc_id = r.json()["id"]
    for start in range(0, 200, 50):
        files = [("files", (f"{i:03d}.jpg", tiny_jpeg(i), "image/jpeg")) for i in range(start, start + 50)]
        assert client.post(f"/api/documents/{doc_id}/files", files=files).status_code == 200
    doc = client.get(f"/api/documents/{doc_id}").json()
    assert doc["page_count"] == 200

    job = client.post(f"/api/documents/{doc_id}/ocr", json={"mode": "ai", "cost_limit_usd": 100}).json()
    assert job["total_pages"] == 200
    assert drain(stop_after=140) == 140
    st = client.get(f"/api/jobs/{job['id']}").json()
    assert st["completed_pages"] == 140 and st["pending_pages"] == 60

    # ---- simulate a crash in the middle of page 141's API request
    from app.core.db import session_scope
    from app.models import ApiCall, OcrRun, Page, PageTask
    from app.workers.worker import claim_task, recover

    tid, jid = claim_task("dying-worker")
    with session_scope() as db:
        t = db.get(PageTask, tid)
        crashed_page = t.page_id
        assert db.get(Page, t.page_id).position == 140  # = page 141
        db.add(ApiCall(owner_id="local", job_id=jid, page_id=t.page_id, purpose="page", model="claude-opus-5-5",
                       status="started", estimated_cost_usd=0.04))

    # ---- restart
    rec = recover(stale_only=False)
    assert rec["tasks"] == 1 and rec["uncertain_calls"] == 1
    st = client.get(f"/api/jobs/{job['id']}").json()
    assert st["api"]["uncertain"] == 1 and st["api"]["uncertain_cost_estimate_usd"] == 0.04
    assert st["completed_pages"] == 140

    calls_before = fake_claude.page_calls
    order = []
    import app.services.ocr_service as svc

    orig = svc.process_page

    def spy(page_id, *a, **k):
        order.append(page_id)
        return orig(page_id, *a, **k)

    monkeypatch.setattr("app.workers.worker.process_page", spy)
    assert drain() == 60
    st = client.get(f"/api/jobs/{job['id']}").json()
    assert st["status"] == "completed" and st["completed_pages"] == 200
    # resumed with page 141 (the interrupted one), then 142, ...
    assert order[0] == crashed_page
    assert fake_claude.page_calls - calls_before == 60  # nothing from pages 1-140 re-sent
    with session_scope() as db:
        runs = dict(db.execute(select(OcrRun.page_id, func.count()).group_by(OcrRun.page_id)).all())
    assert len(runs) == 200 and max(runs.values()) == 1  # every page processed exactly once
    # the possibly-billed interrupted call stays visible and counts toward the limit
    assert st["api"]["uncertain"] == 1
