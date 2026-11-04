"""Claude pricing and cost estimation.

Prices: USD per million tokens, from https://platform.claude.com/docs/en/about-claude/pricing
(checked 2026-11-04). Update PRICING or set CLAUDE_PRICING_OVERRIDE when prices change.
Actual cost is computed from the usage block returned by the API. Anything computed
*before* a call (or for a model missing from this table) is flagged as an estimate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.config import get_settings

PRICING_CHECKED = "2026-11-04"


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float  # 5-minute TTL write


PRICING: dict[str, Price] = {
    "claude-fable-5-1": Price(10.0, 50.0, 0.25, 12.5),
    "claude-fable-5": Price(10.0, 50.0, 1.0, 12.5),
    "claude-opus-5-5": Price(4.0, 20.0, 0.20, 5.0),
    "claude-opus-5": Price(5.0, 25.0, 0.50, 6.25),
    "claude-opus-4-8": Price(5.0, 25.0, 0.50, 6.25),
    "claude-opus-4-7": Price(5.0, 25.0, 0.50, 6.25),
    "claude-opus-4-6": Price(5.0, 25.0, 0.50, 6.25),
    "claude-sonnet-5-5": Price(2.0, 10.0, 0.10, 2.5),
    "claude-sonnet-5": Price(2.0, 10.0, 0.20, 2.5),
    "claude-sonnet-4-6": Price(3.0, 15.0, 0.30, 3.75),
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01, 0.125),  # prompts <= 100K tokens
    "claude-haiku-4-5": Price(1.0, 5.0, 0.10, 1.25),
}


def _overrides() -> dict[str, Price]:
    raw = get_settings().claude_pricing_override
    out: dict[str, Price] = {}
    if not raw:
        return out
    for item in raw.split(";"):
        if "=" not in item:
            continue
        name, vals = item.split("=", 1)
        nums = [float(v) for v in vals.split(",")]
        if len(nums) == 4:
            out[name.strip()] = Price(*nums)
    return out


def price_for(model: str) -> tuple[Price | None, bool]:
    """(price, known). Unknown models fall back to Opus 5.5 pricing, flagged unknown."""
    ov = _overrides()
    if model in ov:
        return ov[model], True
    if model in PRICING:
        return PRICING[model], True
    return None, False


def usage_cost(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> tuple[float, bool]:
    """Cost in USD and whether it is exact per the pricing table (False = estimate)."""
    p, known = price_for(model)
    if p is None:
        p = PRICING["claude-opus-5-5"]
    cost = (
        input_tokens * p.input + output_tokens * p.output + cache_read * p.cache_read + cache_write * p.cache_write
    ) / 1_000_000
    return cost, known


# ----------------------------------------------------------- estimation --
def visual_tokens(w: int, h: int) -> int:
    return math.ceil(w / 28) * math.ceil(h / 28)


def claude_image_size(w: int, h: int) -> tuple[int, int]:
    """Size we send to Claude: fits the model's native high-res limits so the API
    does not resize (keeps our region coordinates valid)."""
    s = get_settings()
    scale = min(1.0, s.claude_long_edge_px / max(w, h))
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    while visual_tokens(nw, nh) > s.claude_max_visual_tokens:
        scale *= 0.98
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    return nw, nh


# Empirical defaults, refined from the ledger once real calls exist.
SYSTEM_PROMPT_TOKENS = 1400  # system prompt + JSON schema
TOKENS_PER_REGION_IN = 18
OUTPUT_TOKENS_PER_REGION = 45  # JSON + text for one line
THINKING_OVERHEAD = 0.6  # extra output tokens fraction (adaptive thinking), rough


@dataclass
class PageEstimate:
    input_tokens: int
    output_tokens: int
    cost_usd: float


def estimate_page(w: int, h: int, regions: int = 25, model: str | None = None, reread_fraction: float = 0.0) -> PageEstimate:
    model = model or get_settings().claude_model
    cw, ch = claude_image_size(w, h)
    inp = visual_tokens(cw, ch) + SYSTEM_PROMPT_TOKENS + regions * TOKENS_PER_REGION_IN
    out = int(regions * OUTPUT_TOKENS_PER_REGION * (1 + THINKING_OVERHEAD))
    if reread_fraction:
        # crop re-recognition of a fraction of lines: ~ (200 visual + 20) tokens per crop
        n = max(1, int(regions * reread_fraction))
        inp += 800 + n * 220
        out += int(n * 40 * (1 + THINKING_OVERHEAD))
    cost, _ = usage_cost(model, inp, out)
    return PageEstimate(inp, out, cost)


def learned_ratio(db, model: str) -> float | None:
    """Ratio actual/estimated from the last successful calls, to calibrate estimates."""
    from sqlalchemy import select

    from app.models import ApiCall

    rows = db.execute(
        select(ApiCall.cost_usd, ApiCall.estimated_cost_usd)
        .where(ApiCall.model == model, ApiCall.status == "succeeded", ApiCall.purpose == "page",
               ApiCall.estimated_cost_usd > 0)
        .order_by(ApiCall.started_at.desc())
        .limit(50)
    ).all()
    if len(rows) < 3:
        return None
    act = sum(r[0] for r in rows)
    est = sum(r[1] for r in rows)
    return act / est if est > 0 else None


# Models offered in the desktop app's settings (current generation).
MODEL_CHOICES = [
    {"id": "claude-opus-5-5", "name": "Claude Opus 5.5", "note": "가장 정확 (기본값)"},
    {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5", "note": "균형 — Opus의 약 절반 비용"},
    {"id": "claude-haiku-5-5", "name": "Claude Haiku 5.5", "note": "가장 저렴 — 정확도는 직접 확인 필요"},
]


def model_choices() -> list[dict]:
    """Choices with an estimated cost per page (typical photo, ~25 lines, AI mode)."""
    out = []
    for m in MODEL_CHOICES:
        e = estimate_page(3024, 4032, regions=25, model=m["id"])
        out.append({**m, "per_page_usd": round(e.cost_usd, 4)})
    return out
