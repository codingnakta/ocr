"""Claude Vision OCR engine (official Anthropic Python SDK).

* API key is read on the backend only (ANTHROPIC_API_KEY).
* Structured outputs via `client.beta.messages.parse(output_format=<Pydantic model>)`.
* Every attempt is written to the ApiCall ledger *before* it is sent. A request that
  dies with a timeout / connection error is recorded as `uncertain` (it may have been
  processed and billed), never silently retried as if it had cost nothing.
* We do our own retry loop (SDK max_retries=0) so each retry is visible in the ledger.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Protocol

import cv2
import numpy as np

from app.core.config import get_settings
from app.ocr.base import CostLimitExceeded, DetectedLine, OcrError
from app.ocr.prompts import (
    NO_REGIONS_INSTRUCTION,
    REGIONS_INSTRUCTION,
    REREAD_INSTRUCTION,
    REREAD_SYSTEM,
    SYSTEM_PROMPT,
    ClaudePageOutput,
    ClaudeRereadOutput,
)
from app.services.cost import claude_image_size, usage_cost, visual_tokens

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept the server-side refusal fallback (`fallbacks: "default"`).
# Haiku models have no server-side fallback: sending the parameter would fail.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}


class Ledger(Protocol):
    def check_budget(self, estimated_cost: float) -> None: ...
    def start(self, purpose: str, model: str, request_hash: str, estimated_cost: float, attempt: int) -> str: ...
    def finish(self, call_id: str, **fields: Any) -> None: ...


class NullLedger:
    """Ledger that only accumulates totals (used by evaluation scripts/tests)."""

    def __init__(self):
        self.calls: list[dict] = []

    def check_budget(self, estimated_cost: float) -> None:
        return None

    def start(self, purpose, model, request_hash, estimated_cost, attempt):
        self.calls.append({"purpose": purpose, "model": model, "status": "started", "attempt": attempt,
                           "estimated_cost_usd": estimated_cost})
        return str(len(self.calls) - 1)

    def finish(self, call_id, **fields):
        self.calls[int(call_id)].update(fields)

    @property
    def total_cost(self) -> float:
        return sum(c.get("cost_usd", 0.0) for c in self.calls)


@dataclass
class SentImage:
    data_b64: str
    media_type: str
    width: int
    height: int
    scale_x: float  # sent px = processed px * scale
    scale_y: float


def encode_for_claude(img_bgr: np.ndarray, quality: int = 92) -> SentImage:
    h, w = img_bgr.shape[:2]
    nw, nh = claude_image_size(w, h)
    if (nw, nh) != (w, h):
        img = cv2.resize(img_bgr, (nw, nh), interpolation=cv2.INTER_AREA)
    else:
        img = img_bgr
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise OcrError("이미지 인코딩 실패")
    data = buf.tobytes()
    if len(data) * 4 / 3 > 9.5 * 1024 * 1024:  # stay under the 10 MB base64 limit
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        data = buf.tobytes()
    return SentImage(base64.standard_b64encode(data).decode("ascii"), "image/jpeg", nw, nh, nw / w, nh / h)


def _bbox_scaled(quad, sx: float, sy: float, w: int, h: int) -> list[int]:
    a = np.asarray(quad, dtype=np.float64)
    x0, y0 = a[:, 0].min() * sx, a[:, 1].min() * sy
    x1, y1 = a[:, 0].max() * sx, a[:, 1].max() * sy
    return [int(max(0, round(x0))), int(max(0, round(y0))), int(min(w, round(x1))), int(min(h, round(y1)))]


class ClaudeEngine:
    def __init__(self, model: str | None = None, effort: str | None = None):
        import anthropic

        s = get_settings()
        if not s.claude_configured:
            raise OcrError("ANTHROPIC_API_KEY가 설정되지 않았습니다 (백엔드 .env).")
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(
            api_key=s.anthropic_api_key.get_secret_value(),  # type: ignore[union-attr]
            base_url=s.anthropic_api_url,
            max_retries=0,
            timeout=s.claude_timeout_s,
        )
        self.model = model or s.claude_model
        self.effort = effort or s.claude_effort
        self.max_tokens = s.claude_max_tokens
        self.attempts = s.claude_max_retries + 1
        self.fallbacks = s.claude_fallbacks.strip() or None

    # ------------------------------------------------------------ calls --
    def _request_kwargs(self, system: str, content: list, output_model) -> dict:
        kw: dict[str, Any] = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=output_model,
            output_config={"effort": self.effort},
        )
        if self.fallbacks and self.model in FALLBACK_MODELS:
            kw["betas"] = [FALLBACK_BETA]
            kw["fallbacks"] = self.fallbacks
        return kw

    def _call(self, purpose: str, system: str, content: list, output_model, est_in: int, est_out: int, ledger: Ledger):
        est_cost, _ = usage_cost(self.model, est_in, est_out)
        ledger.check_budget(est_cost)
        req_hash = hashlib.sha256(
            (self.model + system + repr([c.get("text", c.get("source", {}).get("data", "")[:64]) for c in content])).encode()
        ).hexdigest()
        last_exc: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            if attempt > 1:
                ledger.check_budget(est_cost)
            call_id = ledger.start(purpose, self.model, req_hash, est_cost, attempt)
            t0 = time.monotonic()
            try:
                resp = self.client.beta.messages.parse(**self._request_kwargs(system, content, output_model))
            except (self.anthropic.APITimeoutError, self.anthropic.APIConnectionError) as e:
                # The server may have processed (and billed) this request.
                ledger.finish(call_id, status="uncertain", error=f"{type(e).__name__}: possibly billed",
                              elapsed_s=time.monotonic() - t0)
                last_exc = e
                self._sleep(attempt)
                continue
            except self.anthropic.RateLimitError as e:
                ledger.finish(call_id, status="failed", error="429 rate limited", request_id=_rid(e))
                last_exc = e
                self._sleep(attempt, _retry_after(e))
                continue
            except self.anthropic.APIStatusError as e:
                retryable = e.status_code >= 500 or e.status_code in (408, 409, 529)
                ledger.finish(call_id, status="failed", error=f"{e.status_code}: {str(e.message)[:300]}",
                              request_id=_rid(e))
                last_exc = e
                if retryable:
                    self._sleep(attempt)
                    continue
                if e.status_code in (401, 403):
                    raise OcrError("Claude API 인증 실패: API 키 또는 권한을 확인하세요.") from e
                if e.status_code == 400 and "credit" in str(e.message).lower():
                    raise OcrError("Claude API 크레딧이 부족합니다. Console의 결제/크레딧 설정을 확인하세요.") from e
                raise OcrError(f"Claude API 요청 오류 ({e.status_code}): {str(e.message)[:200]}") from e
            except Exception as e:  # validation errors from parse, etc.
                ledger.finish(call_id, status="failed", error=f"{type(e).__name__}: {str(e)[:300]}")
                raise OcrError(f"Claude 응답 처리 실패: {type(e).__name__}") from e

            u = resp.usage
            served = getattr(resp, "model", None) or self.model
            cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
            details = getattr(u, "output_tokens_details", None)
            thinking = getattr(details, "thinking_tokens", 0) or 0 if details else 0
            cost, known = usage_cost(served, u.input_tokens, u.output_tokens, cache_read, cache_write)
            exact = known and served == self.model
            fields = dict(
                input_tokens=u.input_tokens, output_tokens=u.output_tokens, thinking_tokens=thinking,
                cache_read_tokens=cache_read, cache_write_tokens=cache_write, cost_usd=cost,
                cost_is_estimate=not exact, served_model=served, stop_reason=resp.stop_reason,
                request_id=getattr(resp, "_request_id", None), elapsed_s=time.monotonic() - t0,
            )
            if resp.stop_reason == "refusal":
                ledger.finish(call_id, status="failed", error="refusal", **fields)
                raise OcrError("Claude가 이 페이지 처리를 거절했습니다 (refusal).")
            if resp.stop_reason == "max_tokens":
                ledger.finish(call_id, status="failed", error="max_tokens: output truncated", **fields)
                raise OcrError("출력 길이 제한에 도달했습니다. CLAUDE_MAX_TOKENS를 늘리거나 페이지를 나눠 주세요.")
            parsed = getattr(resp, "parsed_output", None)
            if parsed is None:
                ledger.finish(call_id, status="failed", error="no parsed output", **fields)
                raise OcrError("Claude 응답을 구조화된 결과로 해석하지 못했습니다.")
            ledger.finish(call_id, status="succeeded", **fields)
            return parsed, fields
        raise OcrError(f"Claude API 호출 실패 (재시도 {self.attempts}회): {type(last_exc).__name__}")

    def _sleep(self, attempt: int, retry_after: float | None = None) -> None:
        if attempt >= self.attempts:
            return
        delay = retry_after if retry_after else min(30.0, 2.0 ** attempt + random.uniform(0, 1))
        time.sleep(delay)

    # ------------------------------------------------------------ public --
    def read_page(self, img_bgr: np.ndarray, lines: list[DetectedLine], ledger: Ledger
                  ) -> tuple[ClaudePageOutput, SentImage, dict]:
        sent = encode_for_claude(img_bgr)
        if lines:
            reg_txt = "\n".join(
                f"{ln.key}: {_bbox_scaled(ln.quad, sent.scale_x, sent.scale_y, sent.width, sent.height)}"
                for ln in lines
            )
            instruction = REGIONS_INSTRUCTION.format(w=sent.width, h=sent.height, regions=reg_txt)
        else:
            instruction = NO_REGIONS_INSTRUCTION.format(w=sent.width, h=sent.height)
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": sent.media_type, "data": sent.data_b64}},
            {"type": "text", "text": instruction},
        ]
        est_in = visual_tokens(sent.width, sent.height) + 1400 + 18 * len(lines)
        est_out = max(400, 70 * max(len(lines), 5))
        parsed, usage = self._call("page", SYSTEM_PROMPT, content, ClaudePageOutput, est_in, est_out, ledger)
        return parsed, sent, usage

    def reread_lines(self, crops: list[tuple[str, np.ndarray]], ledger: Ledger, purpose: str = "reread"
                     ) -> tuple[ClaudeRereadOutput, dict]:
        content: list[dict] = [{"type": "text", "text": REREAD_INSTRUCTION}]
        est_in = 600
        for key, crop in crops:
            sent = encode_for_claude(crop, quality=92)
            content.append({"type": "text", "text": f"id={key}"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": sent.media_type,
                                                         "data": sent.data_b64}})
            est_in += visual_tokens(sent.width, sent.height) + 10
        est_out = 300 + 60 * len(crops)
        parsed, usage = self._call(purpose, REREAD_SYSTEM, content, ClaudeRereadOutput, est_in, est_out, ledger)
        return parsed, usage


def _rid(e) -> str | None:
    try:
        return e.response.headers.get("request-id")
    except Exception:
        return None


def _retry_after(e) -> float | None:
    try:
        v = e.response.headers.get("retry-after")
        return min(60.0, float(v)) if v else None
    except Exception:
        return None


_engines: dict[tuple, ClaudeEngine] = {}


def get_claude_engine(model: str | None = None, effort: str | None = None) -> ClaudeEngine:
    key = (model or get_settings().claude_model, effort or get_settings().claude_effort)
    if key not in _engines:
        _engines[key] = ClaudeEngine(*key)
    return _engines[key]
