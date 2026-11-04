"""Page OCR pipeline: preprocess -> detect (+orientation/deskew) -> recognize -> map back.

Output regions carry quads normalized to the *display* image (0..1), ready for storage.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np

from app.imaging.geometry import crop_quad, normalize_quad
from app.imaging.preprocess import (
    DEFAULT_STEPS,
    Preprocessed,
    estimate_skew,
    map_quad_to_display,
    preprocess,
    rotate_quarter,
    rotate_small,
    vertical_fraction,
)
from app.ocr.base import DetectedLine, OcrError, PageOcrResult, RegionResult
from app.ocr.local_engine import LocalOcrEngine, filter_quads, get_local_engine, sort_reading_order

log = logging.getLogger(__name__)

MODES = ("ai", "local", "hybrid")
# Bump when preprocessing / detection / orientation / hybrid logic changes: it is part
# of the OCR cache key, so results produced by older logic are not reused.
PIPELINE_VERSION = "2026-11-04.3"
ORIENT_MIN_P = 0.8  # minimum page-orientation model probability to act on


@dataclass
class PipelineOutput:
    result: PageOcrResult
    processed: np.ndarray
    H: list
    steps: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)


def _detect(local: LocalOcrEngine, img: np.ndarray) -> list[DetectedLine]:
    quads = filter_quads(local.detect(img), img.shape)
    return sort_reading_order([DetectedLine(key="", quad=q) for q in quads])


def _cls_fraction(local: LocalOcrEngine, img: np.ndarray, lines: list[DetectedLine]) -> tuple[float, float, int]:
    """Fraction of lines the 0/180 classifier calls upside down, for the crops as they
    are and for the same crops flipped. A trustworthy verdict flips with the image."""
    import cv2

    crops = [crop_quad(img, ln.quad) for ln in lines[:20]]
    if not crops:
        return 0.0, 0.0, 0
    up, down = local.line_orientation_votes(crops)
    up_f, down_f = local.line_orientation_votes([cv2.rotate(c, cv2.ROTATE_180) for c in crops])
    n = len(crops)
    return down / n, down_f / n, n


def _orient_and_deskew(local: LocalOcrEngine, pp: Preprocessed, cfg: dict) -> list[DetectedLine]:
    from app.ocr.orientation import get_orientation_model

    lines = _detect(local, pp.image)
    if cfg.get("orientation") and len(lines) >= 2:
        model = get_orientation_model()
        doc_rot, doc_p = model.predict(pp.image) if model else (0, 0.0)
        vertical = vertical_fraction([ln.quad for ln in lines]) > 0.6
        rotate_by = 0
        if vertical:
            # text runs vertically: page is sideways. Prefer the page model's 90/270.
            if doc_rot in (90, 270) and doc_p >= ORIENT_MIN_P:
                rotate_by = 360 - doc_rot
            else:
                best = None
                for deg in (90, 270):
                    trial = Preprocessed(image=pp.image, H=pp.H.copy(), steps=list(pp.steps))
                    rotate_quarter(trial, deg)
                    tl = _detect(local, trial.image)
                    d, df, n = _cls_fraction(local, trial.image, tl)
                    score = df - d  # upright crops: few "180" votes, flipped: many
                    if best is None or score > best[0]:
                        best = (score, deg)
                rotate_by = best[1] if best else 0
        else:
            d, df, n = _cls_fraction(local, pp.image, lines)
            cls_says_flipped = n >= 3 and d >= 0.8 and df <= 0.2
            cls_says_upright = n >= 3 and d <= 0.2 and df >= 0.8
            # Rotate only on confident evidence. Measured: the page model gave p=0.50 for
            # an upright photo (wrong) but p>=0.9 when right; the line classifier is only
            # decisive on printed text. Ambiguous pages are left as they are; in AI/hybrid
            # mode Claude reports a needed rotation and the page is corrected then.
            if doc_rot == 180 and doc_p >= ORIENT_MIN_P and not cls_says_upright:
                rotate_by = 180
            elif cls_says_flipped and not (doc_rot == 0 and doc_p >= ORIENT_MIN_P):
                rotate_by = 180
            pp.steps.append({"step": "orientation_check", "page_model": [doc_rot, round(doc_p, 3)],
                             "line_cls_180": round(d, 2), "line_cls_180_flipped": round(df, 2)})
        if rotate_by:
            rotate_quarter(pp, rotate_by)
            lines = _detect(local, pp.image)
    if cfg.get("deskew") and lines:
        ang = estimate_skew([ln.quad for ln in lines])
        if ang is not None and 0.4 <= abs(ang) <= 15:
            rotate_small(pp, ang)
            lines = _detect(local, pp.image)
    return lines


def run_page_ocr(display_bgr: np.ndarray, mode: str, ledger, steps: dict | None = None,
                 claude_model: str | None = None, claude_effort: str | None = None) -> PipelineOutput:
    if mode not in MODES:
        raise OcrError(f"알 수 없는 OCR 모드: {mode}")
    t0 = time.monotonic()
    cfg = {**DEFAULT_STEPS, **(steps or {})}
    timings: dict[str, float] = {}
    pp = preprocess(display_bgr, cfg)
    timings["preprocess"] = time.monotonic() - t0

    local = get_local_engine()
    warnings: list[str] = []
    if local is None:
        if mode == "local":
            raise OcrError("로컬 OCR 엔진이 비활성화되어 있습니다.")
        lines: list[DetectedLine] = []
        warnings.append("로컬 텍스트 검출을 사용할 수 없어 글자 위치를 정할 수 없습니다. PDF 텍스트 레이어에서 제외됩니다.")
    else:
        t = time.monotonic()
        lines = _orient_and_deskew(local, pp, cfg)
        timings["detect"] = time.monotonic() - t
        if local.note:
            warnings.append(local.note)
        if mode in ("local", "hybrid") and lines:
            t = time.monotonic()
            crops = [crop_quad(pp.image, ln.quad) for ln in lines]
            for ln, (txt, score) in zip(lines, local.recognize_crops(crops)):
                ln.local_text, ln.local_score = txt, score
            timings["local_rec"] = time.monotonic() - t

    raw: dict = {"detected": [{"key": ln.key, "quad": ln.quad, "local_text": ln.local_text,
                               "local_score": ln.local_score} for ln in lines]}
    api_calls = 0
    model = None

    if mode == "local":
        regions = [
            RegionResult(key=ln.key, quad=ln.quad, text=ln.local_text or "", engine="local",
                         text_local=ln.local_text, local_score=ln.local_score,
                         flags=(["low_local_score"] if (ln.local_score or 0) < 0.6 else []),
                         uncertain=(ln.local_score or 0) < 0.6)
            for ln in lines
        ]
        if local is not None and not local.supports_hangul:
            warnings.append("현재 로컬 인식 모델은 한글을 지원하지 않습니다. 한글 문서는 AI/하이브리드 모드를 사용하세요.")
    else:
        from app.ocr import hybrid_engine as hy
        from app.ocr.claude_engine import get_claude_engine

        claude = get_claude_engine(claude_model, claude_effort)
        model = claude.model
        t = time.monotonic()
        out, sent, usage = claude.read_page(pp.image, lines, ledger)
        api_calls += 1
        rot = getattr(out, "page_rotation", 0) or 0
        if rot in (90, 180, 270) and cfg.get("orientation", True):
            # Claude says the text is not upright: fix the image and read again (once),
            # instead of keeping a transcription made from a rotated page.
            raw["claude_page_rotated_attempt"] = out.model_dump()
            rotate_quarter(pp, rot)
            lines = _detect(local, pp.image) if local else []
            if local and mode == "hybrid" and lines:
                crops_ = [crop_quad(pp.image, ln.quad) for ln in lines]
                for ln, (txt, score) in zip(lines, local.recognize_crops(crops_)):
                    ln.local_text, ln.local_score = txt, score
            out, sent, usage = claude.read_page(pp.image, lines, ledger)
            api_calls += 1
            warnings.append(f"Claude가 페이지가 {rot}° 회전되어 있다고 판단해 바로 세운 뒤 다시 인식했습니다.")
        timings["claude_page"] = time.monotonic() - t
        raw["claude_page"] = out.model_dump()
        raw["claude_image"] = {"w": sent.width, "h": sent.height}
        regions, w2 = hy.map_claude_regions(out, lines)
        warnings.extend(w2)
        detect_fn = (lambda im: filter_quads(local.detect(im), im.shape)) if local else None
        unboxed = hy.place_unboxed(out, lines, (sent.scale_x, sent.scale_y), detect_fn, pp.image)
        after = {u.key: ub.after_id for u, ub in zip(unboxed, out.unboxed)}
        # Claude's reading order for boxed regions, then insert unboxed ones
        regions = hy.order_with_unboxed(regions + unboxed, after)
        if out.notes:
            warnings.append("Claude 메모: " + out.notes[:300])

        if mode == "hybrid":
            has_hangul_local = bool(local and local.supports_hangul)
            reasons = {r.key: rs for r in regions if r.quad is not None
                       and (rs := hy.suspicious(r, has_hangul_local))}
            if reasons:
                keys = list(reasons)[: hy.MAX_REREAD_PER_PAGE]
                reasons = {k: reasons[k] for k in keys}
                by_key = {r.key: r for r in regions}
                crops = [(k, hy.prepare_crop(pp.image, by_key[k].quad)) for k in keys]
                t = time.monotonic()
                try:
                    rr, _ = claude.reread_lines(crops, ledger)
                    api_calls += 1
                    raw["reread"] = {"reasons": reasons, "result": rr.model_dump()}
                    hy.apply_reread(regions, reasons, rr, has_hangul_local)
                except OcrError as e:
                    warnings.append(f"영역 재인식 실패: {e}")
                    for k in keys:
                        by_key[k].uncertain = True
                        by_key[k].flags.append("reread_failed")
                timings["claude_reread"] = time.monotonic() - t
            for r in regions:
                if r.key in reasons:
                    r.flags.extend(f"check:{x}" for x in reasons[r.key])

    # map back to display coordinates, normalized
    dh, dw = display_bgr.shape[:2]
    for r in regions:
        if r.quad is not None:
            q = map_quad_to_display(r.quad, pp.H)
            r.quad = [[min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)] for x, y in normalize_quad(q, dw, dh)]
    unaligned = sum(1 for r in regions if r.quad is None and r.text.strip())
    if unaligned:
        warnings.append(f"위치를 확인할 수 없는 텍스트 {unaligned}줄은 PDF 텍스트 레이어에서 제외됩니다 (TXT/MD에는 포함).")

    timings["total"] = time.monotonic() - t0
    engine_name = {"ai": "claude", "local": "local", "hybrid": "hybrid"}[mode]
    res = PageOcrResult(engine=engine_name, model=model, regions=regions, warnings=warnings, raw=raw,
                        api_calls=api_calls)
    return PipelineOutput(result=res, processed=pp.image, H=pp.H.tolist(), steps=pp.steps, timings=timings)
