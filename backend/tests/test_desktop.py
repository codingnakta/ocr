"""Desktop-mode settings (model choice, API key endpoint guards) and model-specific
request parameters. No network: the key validation call is not exercised here."""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def desktop_client(env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from tests_helpers import reset_settings

    monkeypatch.setenv("DESKTOP_MODE", "true")
    monkeypatch.setenv("PILGIBOT_CONFIG_FILE", str(tmp_path / "config.json"))
    reset_settings()
    from app.main import create_app

    with TestClient(create_app(), client=("127.0.0.1", 50000)) as c:
        c.headers.update({"X-PB-CSRF": "1"})
        yield c


def test_model_choices_and_selection_persisted(desktop_client, tmp_path):
    s = desktop_client.get("/api/settings").json()
    ids = [m["id"] for m in s["claude"]["model_choices"]]
    assert ids == ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
    prices = {m["id"]: m["per_page_usd"] for m in s["claude"]["model_choices"]}
    assert prices["claude-haiku-5-5"] < prices["claude-sonnet-5-5"] < prices["claude-opus-5-5"]
    r = desktop_client.post("/api/settings/model", json={"model": "claude-haiku-5-5"})
    assert r.status_code == 200
    assert desktop_client.get("/api/settings").json()["claude"]["model"] == "claude-haiku-5-5"
    assert json.loads((tmp_path / "config.json").read_text())["claude_model"] == "claude-haiku-5-5"
    assert desktop_client.post("/api/settings/model", json={"model": "gpt-4"}).status_code == 400


def test_model_endpoint_refused_outside_desktop_mode(client):
    assert client.post("/api/settings/model", json={"model": "claude-haiku-5-5"}).status_code == 403
    assert client.post("/api/settings/api-key", json={"api_key": "sk-ant-" + "x" * 30}).status_code == 403


def test_desktop_endpoints_refuse_remote_clients(env, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from tests_helpers import reset_settings

    monkeypatch.setenv("DESKTOP_MODE", "true")
    monkeypatch.setenv("PILGIBOT_CONFIG_FILE", str(tmp_path / "config.json"))
    reset_settings()
    from app.main import create_app

    with TestClient(create_app(), client=("192.168.0.10", 50000)) as c:
        r = c.post("/api/settings/model", json={"model": "claude-haiku-5-5"}, headers={"X-PB-CSRF": "1"})
        assert r.status_code == 403


@pytest.mark.parametrize("model,expect_fallback", [
    ("claude-opus-5-5", True), ("claude-sonnet-5-5", True), ("claude-haiku-5-5", False),
])
def test_fallback_parameter_only_where_supported(env, model, expect_fallback):
    from app.ocr.claude_engine import ClaudeEngine
    from app.ocr.prompts import ClaudePageOutput

    kw = ClaudeEngine(model=model)._request_kwargs("sys", [], ClaudePageOutput)
    assert ("fallbacks" in kw) is expect_fallback
    assert kw["model"] == model
