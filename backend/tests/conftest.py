"""Test fixtures.

Claude is replaced by `FakeClaude` (no network, no cost) in every test except the
ones marked `real_api`, which run only with RUN_REAL_API=1. A passing mock test
proves the pipeline/queue/PDF logic, NOT real OCR accuracy.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))


def pytest_configure(config):
    config.addinivalue_line("markers", "real_api: calls the real Claude API (costs money; RUN_REAL_API=1)")
    config.addinivalue_line("markers", "slow: slow test")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_REAL_API") == "1":
        return
    skip = pytest.mark.skip(reason="real Claude API test — set RUN_REAL_API=1 (미검증: 실제 API 미호출)")
    for item in items:
        if "real_api" in item.keywords:
            item.add_marker(skip)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh settings, database and storage per test."""
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("AUTH_MODE", "none")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("EMBEDDED_WORKER", "false")
    monkeypatch.setenv("OCR_CLAUDE_MAX_RETRIES", "0")
    if os.environ.get("RUN_REAL_API") != "1":
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-dummy-key-not-real")
    _reset()
    from app.core.db import init_db

    init_db()
    yield tmp_path
    _reset()


def _reset():
    from app.core import config, db
    from app.ocr import claude_engine, local_engine
    from app.services import storage

    config.get_settings.cache_clear()
    db.reset_engine()
    storage.get_storage.cache_clear()
    claude_engine._engines.clear()
    local_engine._engine = None
    local_engine.local_info.cache_clear()
    from app.core.security import limiter

    limiter.reset()


@pytest.fixture()
def client(env):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        c.headers.update({"X-PB-CSRF": "1"})
        yield c


@pytest.fixture()
def fake_claude(monkeypatch):
    from fake_claude import FakeClaude

    fc = FakeClaude()
    import app.ocr.claude_engine as ce

    monkeypatch.setattr(ce, "get_claude_engine", lambda model=None, effort=None: fc)
    return fc


def drain(max_tasks: int = 10_000, stop_after: int | None = None) -> int:
    """Run queued work synchronously (deterministic stand-in for the worker threads)."""
    from app.workers.worker import claim_export, claim_task, handle_task
    from app.services.export_service import run_export

    n = 0
    while n < max_tasks:
        if stop_after is not None and n >= stop_after:
            break
        eid = claim_export("test")
        if eid:
            run_export(eid)
            continue
        t = claim_task("test")
        if not t:
            break
        handle_task(*t)
        n += 1
    return n
