"""Combining local detection with Claude recognition.

Claude does not guarantee exact coordinates, so positions always come from the local
text detector. Claude is told the detected boxes and transcribes each one by id.
The hybrid mode then checks every region and re-reads suspicious ones from exact
crops (one cheap multi-image call per page), where the text-to-box mapping is
guaranteed by construction.

Text that Claude saw but that no detected box covers is never placed at an invented
position: we try to find a detector box near Claude's approximate location; if none is
found the text is kept as *unaligned* (exported to TXT/MD/JSON, shown in the editor,
but not written into the PDF text layer).
"""

from __future__ import annotations

import numpy as np

from app.evaluation.metrics import has_hangul, normalize, similarity, text_width_units
from app.imaging.geometry import crop_quad, overlap_ratio, quad_bbox, quad_metrics
from app.ocr.base import DetectedLine, RegionResult
from app.ocr.prompts import ClaudePageOutput, ClaudeRereadOutput

PLAUSIBLE_MIN = 0.40
PLAUSIBLE_MAX = 2.6
MAX_REREAD_PER_PAGE = 40


def plausibility(text: str, quad) -> float | None:
    """Ratio between expected text width (from character classes) and the box's
    width in line-height units. ~1.0 = plausible, far off = text likely belongs to
    another box (or the box covers more than the text)."""
    t = normalize(text)
    if not t:
        return None
    m = quad_metrics(quad)
    if m["height"] < 1 or m["length"] < 1:
        return None
    box_units = m["length"] / m["height"]
    return text_width_units(t) / max(box_units, 0.3)


def map_claude_regions(out: ClaudePageOutput, lines: list[DetectedLine]) -> tuple[list[RegionResult], list[str]]:
    by_key = {ln.key: ln for ln in lines}
    seen: set[str] = set()
    results: list[RegionResult] = []
    warnings: list[str] = []
    for r in out.regions:
        ln = by_key.get(r.id)
        if ln is None:
            warnings.append(f"Claude가 존재하지 않는 영역 id '{r.id}'를 반환해 무시했습니다.")
            continue
        if r.id in seen:
            warnings.append(f"영역 {r.id}가 중복 반환되어 첫 결과만 사용했습니다.")
            continue
        seen.add(r.id)
        flags = []
        if r.uncertain_parts:
            flags.append("uncertain_parts:" + "|".join(r.uncertain_parts)[:200])
        results.append(RegionResult(
            key=r.id, quad=ln.quad, text=r.text, engine="claude", text_claude=r.text,
            text_local=ln.local_text, local_score=ln.local_score, uncertain=r.uncertain,
            flags=flags, block_index=r.block, block_type=r.block_type,
        ))
    for ln in lines:  # boxes Claude skipped
        if ln.key not in seen:
            results.append(RegionResult(
                key=ln.key, quad=ln.quad, text="", engine="claude", text_claude=None,
                text_local=ln.local_text, local_score=ln.local_score, uncertain=True,
                flags=["claude_missing"],
            ))
    return results, warnings


def place_unboxed(out: ClaudePageOutput, lines: list[DetectedLine], scale: tuple[float, float],
                  detect_fn, img_bgr: np.ndarray) -> list[RegionResult]:
    """Try to give unboxed text a real detector box; else leave it unaligned."""
    sx, sy = scale
    taken = [quad_bbox(ln.quad) for ln in lines]
    res: list[RegionResult] = []
    h, w = img_bgr.shape[:2]
    for i, u in enumerate(out.unboxed):
        quad = None
        source = "none"
        if u.approx_box and len(u.approx_box) == 4 and detect_fn is not None:
            x0, y0, x1, y1 = u.approx_box
            x0, x1 = sorted((x0 / sx, x1 / sx))
            y0, y1 = sorted((y0 / sy, y1 / sy))
            bh = max(10.0, y1 - y0)
            X0, Y0 = int(max(0, x0 - bh)), int(max(0, y0 - bh))
            X1, Y1 = int(min(w, x1 + bh)), int(min(h, y1 + bh))
            if X1 - X0 > 8 and Y1 - Y0 > 8:
                cands = []
                for q in detect_fn(img_bgr[Y0:Y1, X0:X1]):
                    qq = [[p[0] + X0, p[1] + Y0] for p in q]
                    bb = quad_bbox(qq)
                    if any(overlap_ratio(bb, t) > 0.3 for t in taken):
                        continue
                    if overlap_ratio(bb, (x0, y0, x1, y1)) < 0.3:
                        continue
                    cands.append(qq)
                if len(cands) == 1:
                    quad, source = cands[0], "local_det_refined"
                    taken.append(quad_bbox(quad))
        flags = ["unboxed"] + ([] if quad else ["unaligned"])
        if u.uncertain_parts:
            flags.append("uncertain_parts:" + "|".join(u.uncertain_parts)[:200])
        res.append(RegionResult(
            key=f"u{i + 1}", quad=quad, text=u.text, engine="claude", text_claude=u.text,
            position_source=source, uncertain=u.uncertain or quad is None, flags=flags,
            block_index=u.block, block_type=u.block_type,
        ))
    return res


def order_with_unboxed(regions: list[RegionResult], unboxed_after: dict[str, str | None]) -> list[RegionResult]:
    """Insert unboxed lines after the region Claude said they follow."""
    boxed = [r for r in regions if r.key not in unboxed_after]
    extra = [r for r in regions if r.key in unboxed_after]
    out: list[RegionResult] = []
    for r in [e for e in extra if unboxed_after[e.key] is None]:
        out.append(r)
    for r in boxed:
        out.append(r)
        out.extend(e for e in extra if unboxed_after[e.key] == r.key)
    placed = {id(r) for r in out}
    out.extend(e for e in extra if id(e) not in placed)
    return out


def suspicious(r: RegionResult, local_has_hangul: bool) -> list[str]:
    """Reasons to re-read a region from an exact crop."""
    reasons = []
    if "claude_missing" in r.flags:
        reasons.append("claude_missing")
    if r.quad is None:
        return reasons
    p = plausibility(r.text, r.quad)
    if p is not None and not (PLAUSIBLE_MIN <= p <= PLAUSIBLE_MAX):
        reasons.append("length_mismatch")
    lt = (r.text_local or "").strip()
    script_ok = local_has_hangul or not (has_hangul(r.text) or has_hangul(lt))
    if script_ok and lt and (r.local_score or 0) >= 0.6:
        if not r.text.strip():
            reasons.append("empty_but_local_text")
        elif similarity(r.text, lt) < 0.5:
            reasons.append("local_disagree")
    if r.uncertain:
        reasons.append("uncertain")
    return reasons


def prepare_crop(img_bgr: np.ndarray, quad) -> np.ndarray:
    import cv2

    c = crop_quad(img_bgr, quad, pad=0.25)
    h = c.shape[0]
    if h < 64:  # tiny crops read poorly; upscale for Claude
        s = 64 / max(h, 1)
        c = cv2.resize(c, (max(1, int(c.shape[1] * s)), 64), interpolation=cv2.INTER_CUBIC)
    return c


def apply_reread(regions: list[RegionResult], reasons: dict[str, list[str]], out: ClaudeRereadOutput,
                 local_has_hangul: bool) -> dict[str, str]:
    """Merge crop re-reads into regions. Returns {key: reread_text} for audit."""
    by_id = {x.id: x for x in out.lines}
    audit = {}
    for r in regions:
        if r.key not in reasons:
            continue
        rr = by_id.get(r.key)
        if rr is None:
            r.flags.append("reread_missing")
            r.uncertain = True
            continue
        audit[r.key] = rr.text
        first = r.text
        mapping_suspect = any(x in reasons[r.key] for x in ("claude_missing", "length_mismatch", "empty_but_local_text"))
        if normalize(rr.text, ignore_space=True) == normalize(first, ignore_space=True):
            r.flags.append("reread_confirmed")
            # agreement of two independent reads; keep Claude's own uncertainty flag
            r.uncertain = r.uncertain and rr.uncertain
            continue
        lt = r.text_local or ""
        script_ok = local_has_hangul or not (has_hangul(rr.text) or has_hangul(lt))
        prefer_crop = mapping_suspect
        if not prefer_crop and script_ok and lt.strip():
            prefer_crop = similarity(rr.text, lt) > similarity(first, lt) + 0.1
        if prefer_crop:
            r.text = rr.text
            r.engine = "claude_region"
        r.flags.append("reread_differs")
        r.flags.append("alt:" + (first if prefer_crop else rr.text)[:300])
        r.uncertain = True
        if rr.uncertain_parts:
            r.flags.append("uncertain_parts:" + "|".join(rr.uncertain_parts)[:200])
    return audit
