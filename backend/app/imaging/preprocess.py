"""OCR-oriented image preprocessing.

Design rules
* The original upload is never modified; the preprocessed image is stored separately.
* Every geometric step is recorded as a 3x3 matrix so OCR coordinates found on the
  processed image can be mapped back onto the original (display) image.
* Color is preserved: illumination is corrected per channel by dividing by an
  estimated paper background (a flat-field correction), so blue/red/orange ink and
  highlighter marks keep their hue. No binarization is applied to what Claude sees.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.imaging.geometry import apply_h, order_quad, quad_metrics

DEFAULT_STEPS = {
    "document": True,  # paper detection + perspective correction
    "illumination": True,
    "contrast": True,
    "denoise": True,
    "resize": True,
    "deskew": True,  # applied in the OCR pipeline (needs text detection)
    "orientation": True,  # 90/180/270 auto-rotation (needs text detection)
}


@dataclass
class Preprocessed:
    image: np.ndarray  # BGR uint8
    H: np.ndarray  # display px -> processed px
    steps: list[dict] = field(default_factory=list)

    def compose(self, M: np.ndarray, new_img: np.ndarray, step: dict) -> None:
        self.H = M @ self.H
        self.image = new_img
        self.steps.append(step)


# ---------------------------------------------------------------- document --
def find_document_quad(img: np.ndarray) -> np.ndarray | None:
    """Return the paper quad (4x2, original px) or None if not confidently found."""
    h, w = img.shape[:2]
    scale = 900.0 / max(h, w)
    small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    area_total = sh * sw
    candidates: list[np.ndarray] = []

    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    # 1) edges
    edges = cv2.Canny(gray, 40, 120)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=1)
    # 2) brightness mask: paper is usually lighter/less saturated than the desk
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    score = hsv[:, :, 2].astype(np.int16) - hsv[:, :, 1].astype(np.int16)
    score = np.clip(score, 0, 255).astype(np.uint8)
    _, mask = cv2.threshold(score, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))

    for src in (edges, mask):
        cnts, _ = cv2.findContours(src, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:5]:
            peri = cv2.arcLength(c, True)
            for eps in (0.015, 0.02, 0.03, 0.04):
                approx = cv2.approxPolyDP(c, eps * peri, True)
                if len(approx) == 4 and cv2.isContourConvex(approx):
                    candidates.append(approx.reshape(4, 2).astype(np.float64))
                    break

    best, best_area = None, 0.0
    for q in candidates:
        a = cv2.contourArea(q.astype(np.float32))
        frac = a / area_total
        if 0.25 <= frac <= 0.97 and a > best_area:
            # reject degenerate shapes (very thin / extreme angles)
            qq = np.array(order_quad(q))
            ang_ok = True
            for i in range(4):
                v1 = qq[i - 1] - qq[i]
                v2 = qq[(i + 1) % 4] - qq[i]
                cosang = abs(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9))
                if cosang > 0.5:  # corner angle outside ~60..120 deg
                    ang_ok = False
            if ang_ok:
                best, best_area = qq, a
    if best is None:
        return None
    return best / scale


def warp_document(img: np.ndarray, quad: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    tl, tr, br, bl = quad
    wa = np.linalg.norm(tr - tl)
    wb = np.linalg.norm(br - bl)
    ha = np.linalg.norm(bl - tl)
    hb = np.linalg.norm(br - tr)
    W, Ht = int(round(max(wa, wb))), int(round(max(ha, hb)))
    dst = np.float32([[0, 0], [W - 1, 0], [W - 1, Ht - 1], [0, Ht - 1]])
    M = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    out = cv2.warpPerspective(img, M, (W, Ht), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return out, M.astype(np.float64)


# ------------------------------------------------------------ photometric --
def correct_illumination(img: np.ndarray) -> np.ndarray:
    """Flat-field correction per channel. Ink/highlighter hue ratios are kept."""
    h, w = img.shape[:2]
    scale = 512.0 / max(h, w)
    small = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    k = max(15, (int(min(small.shape[:2]) / 12) | 1))
    # dilation (max filter) removes thin dark strokes; closing keeps paper level
    bg = cv2.dilate(small, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.medianBlur(bg, k if k <= 255 else 255)
    bg = cv2.GaussianBlur(bg, (0, 0), k / 3)
    bg = cv2.resize(bg, (w, h), interpolation=cv2.INTER_CUBIC).astype(np.float32)
    bg = np.maximum(bg, 30.0)
    # target paper level: bright but not clipped
    out = img.astype(np.float32) / bg * 245.0
    return np.clip(out, 0, 255).astype(np.uint8)


def stretch_contrast(img: np.ndarray, lo_pct: float = 0.3, hi_pct: float = 99.5) -> np.ndarray:
    """Linear stretch with the SAME affine map on all channels.

    Applying an identical offset/scale to B, G and R keeps every pixel's hue exactly
    (hue depends only on channel differences ratios), so colored ink and highlighter
    keep their color; only lightness/contrast changes."""
    darkest = img.min(axis=2)
    lo = float(np.percentile(darkest, lo_pct))
    hi = float(np.percentile(img.max(axis=2), hi_pct))
    if hi - lo < 30:
        return img
    lo = min(lo, 60.0)  # never push mid-tones (e.g. orange/yellow ink) to black
    out = (img.astype(np.float32) - lo) * (255.0 / (hi - lo))
    return np.clip(out, 0, 255).astype(np.uint8)


def denoise(img: np.ndarray) -> np.ndarray:
    if img.shape[0] * img.shape[1] > 9_000_000:
        return cv2.bilateralFilter(img, 5, 25, 5)
    return cv2.fastNlMeansDenoisingColored(img, None, 3, 3, 7, 15)


# ---------------------------------------------------------------- driver --
def preprocess(img_bgr: np.ndarray, steps: dict | None = None) -> Preprocessed:
    cfg = {**DEFAULT_STEPS, **(steps or {})}
    pp = Preprocessed(image=img_bgr.copy(), H=np.eye(3))

    if cfg["resize"]:
        h, w = pp.image.shape[:2]
        long_edge = max(h, w)
        target = None
        if long_edge > 3300:
            target = 3300
        elif long_edge < 1400:
            target = min(1800, long_edge * 2)
        if target and target != long_edge:
            s = target / long_edge
            interp = cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC
            out = cv2.resize(pp.image, (int(round(w * s)), int(round(h * s))), interpolation=interp)
            sx, sy = out.shape[1] / w, out.shape[0] / h
            pp.compose(np.diag([sx, sy, 1.0]), out, {"step": "resize", "scale": round(s, 4)})

    if cfg["document"]:
        q = find_document_quad(pp.image)
        if q is not None:
            out, M = warp_document(pp.image, q)
            pp.compose(M, out, {"step": "document", "quad": np.round(q, 1).tolist()})

    if cfg["illumination"]:
        pp.image = correct_illumination(pp.image)
        pp.steps.append({"step": "illumination"})
    if cfg["contrast"]:
        pp.image = stretch_contrast(pp.image)
        pp.steps.append({"step": "contrast"})
    if cfg["denoise"]:
        pp.image = denoise(pp.image)
        pp.steps.append({"step": "denoise"})
    return pp


# ------------------------------------------- detection-driven corrections --
def estimate_skew(quads: list, min_boxes: int = 3) -> float | None:
    """Median baseline angle (deg) of detected text lines, weighted by length."""
    angs, weights = [], []
    for q in quads:
        m = quad_metrics(q)
        if m["length"] < 2.5 * max(m["height"], 1):  # only clearly horizontal lines
            continue
        a = m["angle"]
        if abs(a) > 20:
            continue
        angs.append(a)
        weights.append(m["length"])
    if len(angs) < min_boxes:
        return None
    order = np.argsort(angs)
    a = np.array(angs)[order]
    wts = np.array(weights)[order]
    cum = np.cumsum(wts)
    return float(a[np.searchsorted(cum, cum[-1] / 2)])


def rotate_small(pp: Preprocessed, angle_deg: float) -> None:
    """Rotate so that text at `angle_deg` becomes horizontal (expanding canvas)."""
    h, w = pp.image.shape[:2]
    R = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
    cos, sin = abs(R[0, 0]), abs(R[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    R[0, 2] += nw / 2 - w / 2
    R[1, 2] += nh / 2 - h / 2
    out = cv2.warpAffine(pp.image, R, (nw, nh), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
    pp.compose(np.vstack([R, [0, 0, 1]]), out, {"step": "deskew", "angle": round(angle_deg, 2)})


def rotate_quarter(pp: Preprocessed, degrees_cw: int) -> None:
    from app.imaging.geometry import rotation_h

    h, w = pp.image.shape[:2]
    code = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[degrees_cw]
    out = cv2.rotate(pp.image, code)
    pp.compose(rotation_h(degrees_cw, w, h), out, {"step": "orientation", "rotate_cw": degrees_cw})


def vertical_fraction(quads: list) -> float:
    if not quads:
        return 0.0
    v = 0
    for q in quads:
        m = quad_metrics(q)
        a = abs(m["angle"])
        if 60 < a < 120 or m["height"] > 2.0 * m["length"]:
            v += 1
    return v / len(quads)


def map_quad_to_display(quad, H: np.ndarray) -> list[list[float]]:
    """Map a quad from processed px back to display px."""
    Hinv = np.linalg.inv(H)
    return apply_h(Hinv, np.asarray(quad, dtype=np.float64)).tolist()


def hue_stats(img_bgr: np.ndarray, mask: np.ndarray) -> tuple[float, float]:
    """Mean hue (circular, degrees) and mean saturation of masked pixels. For tests."""
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    hvals = hsv[:, :, 0][mask].astype(np.float64) * 2 * math.pi / 180
    s = hsv[:, :, 1][mask].astype(np.float64)
    ang = math.degrees(math.atan2(np.sin(hvals).mean(), np.cos(hvals).mean())) % 360
    return ang, float(s.mean())
