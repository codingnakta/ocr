"""Upload validation, CSRF, auth and per-user isolation."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

from app.services.ingest import sanitize_filename, sniff_mime


def png_bytes(w=200, h=200) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(b, "PNG")
    return b.getvalue()


def test_filename_sanitized():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\win.ini") == "win.ini"
    assert sanitize_filename('a<b>c:"d|e?.jpg') == "abcde.jpg"
    assert sanitize_filename("\x00\x1fname.png") == "name.png"
    assert sanitize_filename("") == "upload"


def test_magic_bytes_not_extension():
    assert sniff_mime(b"MZ\x90\x00 executable") is None
    assert sniff_mime(png_bytes()[:64]) == "image/png"


def test_rejects_disguised_corrupt_and_encrypted(client):
    pdf = pymupdf.open()
    pdf.new_page()
    enc = pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    files = [
        ("files", ("virus.jpg", b"MZ\x90\x00 this is not an image", "image/jpeg")),
        ("files", ("broken.pdf", b"%PDF-1.7\n garbage garbage", "application/pdf")),
        ("files", ("locked.pdf", enc, "application/pdf")),
        ("files", ("truncated.png", png_bytes()[:100], "image/png")),
        ("files", ("ok.png", png_bytes(), "image/png")),
    ]
    r = client.post("/api/documents", files=files)
    assert r.status_code == 201
    body = r.json()
    assert body["page_count"] == 1  # only the valid file was added; server kept running
    errs = {e["file"]: e["error"] for e in body["upload"]["errors"]}
    assert set(errs) == {"virus.jpg", "broken.pdf", "locked.pdf", "truncated.png"}
    assert "암호" in errs["locked.pdf"]


def test_file_size_limit(client, monkeypatch):
    from app.core import config

    monkeypatch.setenv("MAX_FILE_MB", "1")
    config.get_settings.cache_clear()
    big = png_bytes(3000, 3000)
    import numpy as np

    arr = (np.random.default_rng(0).random((900, 900, 3)) * 255).astype("uint8")
    b = io.BytesIO()
    Image.fromarray(arr).save(b, "PNG")
    big = b.getvalue()
    assert len(big) > 1024 * 1024
    r = client.post("/api/documents", files=[("files", ("big.png", big, "image/png"))])
    assert "너무 큽니다" in r.json()["upload"]["errors"][0]["error"]


def test_pdf_page_classification(client):
    pdf = pymupdf.open()
    p1 = pdf.new_page()
    p1.insert_text((72, 72), "This page already has a real text layer that is long enough.", fontsize=11)
    p2 = pdf.new_page()
    b = io.BytesIO()
    Image.new("RGB", (800, 1000), (240, 240, 240)).save(b, "JPEG")
    p2.insert_image(p2.rect, stream=b.getvalue())
    pdf.new_page()  # empty
    r = client.post("/api/documents", files=[("files", ("mixed.pdf", pdf.tobytes(), "application/pdf"))])
    pages = r.json()["pages"]
    assert [p["content_type"] for p in pages] == ["text", "image_only", "empty"]
    assert [p["ocr_status"] for p in pages] == ["skipped", "none", "skipped"]


def test_csrf_header_required(client):
    r = client.post("/api/documents", json={"title": "x"}, headers={"X-PB-CSRF": ""})
    assert r.status_code == 403


def test_api_key_never_exposed(client):
    body = client.get("/api/settings").text
    assert "sk-ant" not in body


@pytest.fixture()
def pw_client(env, monkeypatch):
    from fastapi.testclient import TestClient

    from tests_helpers import reset_settings

    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("SESSION_SECRET", "test-secret-0123456789abcdef")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct horse battery")
    reset_settings()
    from app.main import create_app

    with TestClient(create_app()) as c:
        c.headers.update({"X-PB-CSRF": "1"})
        yield c


def test_password_mode_requires_login_and_isolates_users(pw_client):
    c = pw_client
    assert c.get("/api/documents").status_code == 401
    assert c.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    r = c.post("/api/auth/login", json={"username": "admin", "password": "correct horse battery"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    doc = c.post("/api/documents", files=[("files", ("a.png", png_bytes(), "image/png"))]).json()

    # a second user cannot see or download the admin's document
    from app.core.db import session_scope
    from app.core.security import hash_password
    from app.models import User

    with session_scope() as db:
        db.add(User(username="bob", password_hash=hash_password("bob-password")))
    c.post("/api/auth/logout")
    c.cookies.clear()
    c.post("/api/auth/login", json={"username": "bob", "password": "bob-password"})
    assert c.get(f"/api/documents/{doc['id']}").status_code == 404
    assert c.get(f"/api/documents/{doc['id']}/pages/1/thumbnail").status_code == 404
    assert c.get(f"/api/documents/{doc['id']}/download?format=pdf").status_code == 404
    assert c.delete(f"/api/documents/{doc['id']}").status_code == 404
    assert c.get("/api/documents").json()["documents"] == []


def test_login_rate_limited(pw_client):
    codes = [pw_client.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code
             for _ in range(12)]
    assert 429 in codes


def test_production_refuses_no_auth(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_MODE", "none")
    with pytest.raises(Exception):
        Settings()


def test_log_redaction():
    import logging

    from app.core.logging import RedactFilter

    rec = logging.LogRecord("x", logging.INFO, "f", 1, "key=sk-ant-api03-abcdefghijklmnop123 header x-api-key: sk-ant-zzzzzzzzzzzzzz", (), None)
    RedactFilter().filter(rec)
    assert "sk-ant" not in rec.getMessage()
