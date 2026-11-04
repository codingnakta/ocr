"""Free local OCR via RapidOCR (PaddleOCR models on ONNX Runtime).

Roles: text-line detection (coordinates), local recognition, and orientation hints.

Model selection
* Detection: RapidOCR's bundled PP-OCRv6 small detector (shipped inside the wheel,
  script-agnostic, works on Hangul) unless LOCAL_OCR_DET_MODEL is set.
* Recognition: Korean PP-OCRv5 mobile (`LangRec.KOREAN`), fetched by RapidOCR's model
  manager on first use. If it cannot be loaded (offline / blocked network) we fall
  back to the bundled multilingual model, which has NO Hangul; `supports_hangul`
  then reports False so the hybrid engine does not compare Korean text against it.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from app.core.config import get_settings
from app.imaging.geometry import crop_quad, order_quad
from app.ocr.base import DetectedLine

log = logging.getLogger(__name__)


@dataclass
class LocalInfo:
    available: bool
    det_model: str
    rec_model: str
    supports_hangul: bool
    note: str = ""


class LocalOcrEngine:
    def __init__(self):
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        s = get_settings()
        base = {
            "Global.log_level": "warning",
            "Global.text_score": 0.0,  # keep every detected line; scores are reported
            "Global.max_side_len": 2400,
            "Det.box_thresh": 0.45,
            "Det.unclip_ratio": 1.6,
        }
        if s.local_ocr_det_model:
            base["Det.model_path"] = str(s.local_ocr_det_model)
        if s.local_ocr_cls_model:
            base["Cls.model_path"] = str(s.local_ocr_cls_model)
        self._lock = threading.Lock()  # onnxruntime sessions are thread-safe, rapidocr state is not
        self._RapidOCR = RapidOCR
        self.note = ""

        rec_params: dict = {}
        if s.local_ocr_rec_model:
            rec_params = {"Rec.model_path": str(s.local_ocr_rec_model)}
            if s.local_ocr_rec_keys:
                rec_params["Rec.rec_keys_path"] = str(s.local_ocr_rec_keys)
            rec_name = s.local_ocr_rec_model.name
        elif s.local_ocr_rec_lang == "korean":
            rec_params = {
                "Rec.lang_type": LangRec.KOREAN,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
                "Global.model_root_dir": str(s.local_ocr_model_dir / "rapidocr"),
            }
            rec_name = "korean_PP-OCRv5_rec_mobile"
        else:
            rec_name = "bundled PP-OCRv6 small (multilingual)"

        self.engine = RapidOCR(params={**base, **rec_params})
        try:
            self.engine._load_rec_model()
            if self.engine.text_rec is None:
                raise RuntimeError("recognizer not loaded")
        except Exception as e:  # network blocked / missing file
            log.warning("local rec model %s unavailable (%s); using bundled fallback", rec_name, type(e).__name__)
            self.note = f"{rec_name} 모델을 불러오지 못해 기본 모델(한글 미지원)로 대체했습니다."
            self.engine = RapidOCR(params=base)
            self.engine._load_rec_model()
            rec_name = "bundled PP-OCRv6 small (multilingual)"
        self.rec_model = rec_name
        self.det_model = s.local_ocr_det_model.name if s.local_ocr_det_model else "bundled PP-OCRv6 small det"
        self.supports_hangul = self._rec_has_hangul()

    def _rec_has_hangul(self) -> bool:
        rec = self.engine.text_rec
        chars = None
        for attr in ("character", "characters"):
            chars = getattr(getattr(rec, "postprocess_op", None), attr, None) or getattr(rec, attr, None)
            if chars:
                break
        if not chars:
            return False
        return any(isinstance(c, str) and len(c) == 1 and "가" <= c <= "힣" for c in chars)

    def info(self) -> LocalInfo:
        return LocalInfo(True, self.det_model, self.rec_model, self.supports_hangul, self.note)

    # ------------------------------------------------------------------
    def detect(self, img_bgr: np.ndarray) -> list[list[list[float]]]:
        with self._lock:
            out = self.engine(img_bgr, use_det=True, use_cls=False, use_rec=False)
        boxes = getattr(out, "boxes", None)
        if boxes is None:
            return []
        return [order_quad(b) for b in np.asarray(boxes).tolist()]

    def recognize_page(self, img_bgr: np.ndarray) -> list[DetectedLine]:
        """Detect lines, then recognize each crop. Unlike RapidOCR's own pipeline we
        keep lines whose recognition is empty: their *position* is still useful."""
        quads = filter_quads(self.detect(img_bgr), img_bgr.shape)
        lines = sort_reading_order([DetectedLine(key="", quad=q) for q in quads])
        if not lines:
            return []
        crops = [crop_quad(img_bgr, ln.quad) for ln in lines]
        for ln, (txt, score) in zip(lines, self.recognize_crops(crops)):
            ln.local_text, ln.local_score = txt, score
        return lines

    def recognize_crops(self, crops: list[np.ndarray]) -> list[tuple[str, float | None]]:
        from rapidocr.ch_ppocr_rec.typings import TextRecInput

        if not crops:
            return []
        with self._lock:
            cls = self.engine._load_cls_model()
            cls_out = cls(crops)
            imgs = list(getattr(cls_out, "img_list", None) or crops)
            rec = self.engine._load_rec_model()
            out = rec(TextRecInput(img=imgs))
        txts = list(out.txts or [""] * len(crops))
        scores = list(out.scores or [None] * len(crops))
        return [(txts[i] if i < len(txts) else "", float(scores[i]) if i < len(scores) and scores[i] is not None else None)
                for i in range(len(crops))]

    def line_orientation_votes(self, crops: list[np.ndarray]) -> tuple[int, int]:
        """Vote counts (upright, upside-down) from the 0/180 text-line classifier."""
        if not crops:
            return 0, 0
        with self._lock:
            cls = self.engine._load_cls_model()
            out = cls(crops)
        res = getattr(out, "cls_res", None) or []
        up = sum(1 for lbl, sc in res if str(lbl) == "0")
        down = sum(1 for lbl, sc in res if str(lbl) == "180" and sc >= 0.8)
        return up, down


def filter_quads(quads: list, shape) -> list:
    """Drop degenerate detections (slivers, specks)."""
    from app.imaging.geometry import quad_metrics

    h, w = shape[:2]
    min_side = max(6.0, min(h, w) * 0.004)
    out = []
    for q in quads:
        m = quad_metrics(q)
        if m["height"] < min_side or m["length"] < min_side:
            continue
        out.append(q)
    return out


def sort_reading_order(lines: list[DetectedLine]) -> list[DetectedLine]:
    """Top-to-bottom, left-to-right with tolerance for lines on the same row."""
    if not lines:
        return lines

    def bbox(q):
        a = np.asarray(q)
        return a[:, 0].min(), a[:, 1].min(), a[:, 0].max(), a[:, 1].max()

    items = [(ln, bbox(ln.quad)) for ln in lines]
    items.sort(key=lambda t: (t[1][1] + t[1][3]) / 2)
    rows: list[list] = []
    for it in items:
        cy = (it[1][1] + it[1][3]) / 2
        h = it[1][3] - it[1][1]
        if rows:
            last = rows[-1]
            lcy = np.mean([(b[1] + b[3]) / 2 for _, b in last])
            lh = np.mean([b[3] - b[1] for _, b in last])
            if abs(cy - lcy) < 0.5 * min(h, lh):
                last.append(it)
                continue
        rows.append([it])
    out = []
    for row in rows:
        row.sort(key=lambda t: t[1][0])
        out.extend(ln for ln, _ in row)
    for i, ln in enumerate(out):
        ln.key = f"r{i + 1}"
    return out


_engine: LocalOcrEngine | None = None
_engine_lock = threading.Lock()


def get_local_engine() -> LocalOcrEngine | None:
    global _engine
    if not get_settings().local_ocr_enabled:
        return None
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = LocalOcrEngine()
    return _engine


@lru_cache
def local_info() -> dict:
    e = get_local_engine()
    if e is None:
        return {"available": False}
    i = e.info()
    return i.__dict__
