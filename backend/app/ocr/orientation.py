"""Whole-page orientation (0/90/180/270) classifier.

Model: RapidOrientation (Apache-2.0, ONNX), shipped in the PyPI wheel `onnxocr`
(fetched by scripts/fetch_assets.py into models_data/onnxocr/).

Why not the per-line 0/180 classifier alone? Measured on our samples, PaddleOCR's
text-line classifier (trained on printed Chinese) is decisive on printed Latin text
but close to random on Korean handwriting (it voted "upside down" for 4/6 lines of
an upright handwritten page and also 4/6 for the same page rotated 180°), which led
to an upright page being flipped. The document-level model was right on all 8 test
cases, so it is the primary signal; the line classifier only counts when decisive.
"""

from __future__ import annotations

import logging
import threading
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from app.core.config import get_settings

log = logging.getLogger(__name__)
LABELS = (0, 90, 180, 270)


class DocOrientation:
    def __init__(self, path: Path):
        import onnxruntime as ort

        self.sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name
        self.lock = threading.Lock()

    def predict(self, img_bgr: np.ndarray) -> tuple[int, float]:
        """(degrees the content is rotated CLOCKWISE from upright, probability)."""
        h, w = img_bgr.shape[:2]
        s = 256.0 / min(h, w)
        im = cv2.resize(img_bgr, (max(224, int(w * s)), max(224, int(h * s))), interpolation=cv2.INTER_AREA)
        H, W = im.shape[:2]
        t, left = (H - 224) // 2, (W - 224) // 2
        im = im[t:t + 224, left:left + 224].astype(np.float32) / 255.0
        im = (im - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
        with self.lock:
            out = self.sess.run(None, {self.inp: im.transpose(2, 0, 1)[None]})[0][0].astype(np.float64)
        if not (np.all(out >= 0) and abs(out.sum() - 1) < 1e-3):
            out = np.exp(out - out.max())
            out /= out.sum()
        i = int(out.argmax())
        return LABELS[i], float(out[i])


@lru_cache(maxsize=1)
def get_orientation_model() -> DocOrientation | None:
    s = get_settings()
    candidates = [s.orientation_model] if s.orientation_model else []
    candidates.append(s.local_ocr_model_dir / "onnxocr" / "rapid_orientation.onnx")
    for p in candidates:
        if p and Path(p).exists():
            try:
                return DocOrientation(Path(p))
            except Exception as e:  # corrupt file etc.
                log.warning("orientation model failed to load: %s", type(e).__name__)
    return None
