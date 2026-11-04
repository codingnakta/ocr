"""Deterministic stand-in for ClaudeEngine (no network). Goes through the same
ledger protocol (check_budget -> start -> finish) so cost/limit logic is exercised."""

from __future__ import annotations

import threading

from app.ocr.base import OcrError
from app.ocr.claude_engine import SentImage
from app.ocr.prompts import ClaudeLineRead, ClaudePageOutput, ClaudeRegion, ClaudeRereadOutput, ClaudeUnboxed
from app.services.cost import usage_cost


class FakeClaude:
    model = "claude-opus-5-5"
    effort = "medium"

    def __init__(self):
        self.calls = 0
        self.page_calls = 0
        self.reread_calls = 0
        self.fail_pages: set[int] = set()  # 1-based page_call numbers to fail
        self.crash_pages: set[int] = set()  # raise a non-OcrError (simulated crash)
        self.texts: list[list[str]] | None = None  # per page_call, region texts
        self.input_tokens = 6000
        self.output_tokens = 500
        self.lock = threading.Lock()
        self.seen_region_counts: list[int] = []

    def _cost(self):
        return usage_cost(self.model, self.input_tokens, self.output_tokens)[0]

    def _bill(self, ledger, purpose: str, fail: bool = False):
        cost = self._cost()
        cid = ledger.start(purpose, self.model, "fake", cost, 1)
        if fail:
            ledger.finish(cid, status="failed", error="500: simulated server error")
            raise OcrError("Claude API 호출 실패 (시뮬레이션)")
        ledger.finish(cid, status="succeeded", input_tokens=self.input_tokens, output_tokens=self.output_tokens,
                      cost_usd=cost, served_model=self.model, stop_reason="end_turn")
        return {"cost_usd": cost}

    def read_page(self, img, lines, ledger):
        ledger.check_budget(self._cost())  # like the real engine: check before sending
        with self.lock:
            self.calls += 1
            self.page_calls += 1
            n = self.page_calls
        if n in self.crash_pages:
            raise RuntimeError("simulated crash")
        usage = self._bill(ledger, "page", fail=n in self.fail_pages)
        self.seen_region_counts.append(len(lines))
        texts = self.texts[(n - 1) % len(self.texts)] if self.texts else None
        regions = []
        for i, ln in enumerate(lines):
            t = texts[i] if texts and i < len(texts) else f"페이지{n} 줄{i + 1} 텍스트"
            regions.append(ClaudeRegion(id=ln.key, text=t, uncertain=False, uncertain_parts=[], block=i + 1,
                                        block_type="paragraph"))
        unboxed = []
        if not lines:
            for i, t in enumerate(texts or [f"페이지{n} 위치없는 줄"]):
                unboxed.append(ClaudeUnboxed(text=t, uncertain=False, uncertain_parts=[], after_id=None,
                                             approx_box=None, block=i + 1, block_type="paragraph"))
        h, w = img.shape[:2]
        out = ClaudePageOutput(regions=regions, unboxed=unboxed, notes="")
        return out, SentImage("", "image/jpeg", w, h, 1.0, 1.0), usage

    def reread_lines(self, crops, ledger, purpose="reread"):
        ledger.check_budget(self._cost())
        with self.lock:
            self.calls += 1
            self.reread_calls += 1
        usage = self._bill(ledger, purpose)
        return ClaudeRereadOutput(lines=[ClaudeLineRead(id=k, text="재인식", uncertain=False, uncertain_parts=[])
                                         for k, _ in crops]), usage
