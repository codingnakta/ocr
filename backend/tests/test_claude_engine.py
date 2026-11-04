"""ClaudeEngine request/ledger behaviour with a mocked SDK client (no network)."""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import numpy as np
import pytest

from app.ocr.base import CostLimitExceeded, DetectedLine, OcrError
from app.ocr.claude_engine import ClaudeEngine, NullLedger, encode_for_claude
from app.ocr.prompts import ClaudePageOutput, ClaudeRegion


def _req():
    import httpx2

    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _resp(status: int):
    import httpx2

    return httpx2.Response(status, request=_req(), headers={"request-id": "req_test"})


def ok_response(stop="end_turn"):
    parsed = ClaudePageOutput(regions=[ClaudeRegion(id="r1", text="안녕하세요", uncertain=False, uncertain_parts=[],
                                                    block=1, block_type="paragraph")], unboxed=[], notes="")
    usage = SimpleNamespace(input_tokens=5000, output_tokens=400, cache_read_input_tokens=0,
                            cache_creation_input_tokens=0, output_tokens_details=SimpleNamespace(thinking_tokens=120))
    return SimpleNamespace(parsed_output=parsed, usage=usage, model="claude-opus-5-5", stop_reason=stop,
                           _request_id="req_1")


@pytest.fixture()
def engine(env, monkeypatch):
    e = ClaudeEngine()
    monkeypatch.setattr(e, "_sleep", lambda *a, **k: None)
    e.attempts = 3
    return e


def img():
    return np.full((600, 400, 3), 255, np.uint8)


def lines():
    return [DetectedLine("r1", [[10, 10], [300, 10], [300, 50], [10, 50]])]


def test_success_records_usage_and_cost(engine, monkeypatch):
    seen = {}

    def parse(**kw):
        seen.update(kw)
        return ok_response()

    monkeypatch.setattr(engine.client.beta.messages, "parse", parse)
    led = NullLedger()
    out, sent, usage = engine.read_page(img(), lines(), led)
    assert out.regions[0].text == "안녕하세요"
    c = led.calls[0]
    assert c["status"] == "succeeded" and c["thinking_tokens"] == 120
    assert c["cost_usd"] == pytest.approx(5000 * 4e-6 + 400 * 20e-6)
    assert seen["model"] == "claude-opus-5-5" and seen["output_config"] == {"effort": "medium"}
    assert seen["fallbacks"] == "default"
    assert "r1: [" in seen["messages"][0]["content"][1]["text"]  # region list sent
    assert "temperature" not in seen and "thinking" not in seen


def test_timeout_is_recorded_as_uncertain_then_retried(engine, monkeypatch):
    calls = {"n": 0}

    def parse(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise anthropic.APITimeoutError(request=_req())
        return ok_response()

    monkeypatch.setattr(engine.client.beta.messages, "parse", parse)
    led = NullLedger()
    engine.read_page(img(), lines(), led)
    assert [c["status"] for c in led.calls] == ["uncertain", "succeeded"]
    assert "possibly billed" in led.calls[0]["error"]


def test_bad_request_not_retried(engine, monkeypatch):
    calls = {"n": 0}

    def parse(**kw):
        calls["n"] += 1
        raise anthropic.BadRequestError("bad image", response=_resp(400), body=None)

    monkeypatch.setattr(engine.client.beta.messages, "parse", parse)
    with pytest.raises(OcrError):
        engine.read_page(img(), lines(), NullLedger())
    assert calls["n"] == 1


def test_server_error_retried_until_exhausted(engine, monkeypatch):
    def parse(**kw):
        raise anthropic.InternalServerError("boom", response=_resp(500), body=None)

    monkeypatch.setattr(engine.client.beta.messages, "parse", parse)
    led = NullLedger()
    with pytest.raises(OcrError):
        engine.read_page(img(), lines(), led)
    assert [c["status"] for c in led.calls] == ["failed"] * 3


def test_refusal_and_max_tokens(engine, monkeypatch):
    monkeypatch.setattr(engine.client.beta.messages, "parse", lambda **kw: ok_response("refusal"))
    with pytest.raises(OcrError, match="거절"):
        engine.read_page(img(), lines(), NullLedger())
    monkeypatch.setattr(engine.client.beta.messages, "parse", lambda **kw: ok_response("max_tokens"))
    with pytest.raises(OcrError, match="출력 길이"):
        engine.read_page(img(), lines(), NullLedger())


def test_budget_checked_before_sending(engine, monkeypatch):
    sent = {"n": 0}

    def parse(**kw):
        sent["n"] += 1
        return ok_response()

    class Broke(NullLedger):
        def check_budget(self, estimated_cost):
            raise CostLimitExceeded("limit")

    monkeypatch.setattr(engine.client.beta.messages, "parse", parse)
    with pytest.raises(CostLimitExceeded):
        engine.read_page(img(), lines(), Broke())
    assert sent["n"] == 0


def test_image_sized_to_native_limits(env):
    big = np.full((4032, 3024, 3), 200, np.uint8)
    s = encode_for_claude(big)
    assert max(s.width, s.height) <= 2576
    assert -(-s.width // 28) * -(-s.height // 28) <= 4784
    small = encode_for_claude(np.full((800, 600, 3), 200, np.uint8))
    assert (small.width, small.height) == (600, 800)
