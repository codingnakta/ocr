"""Geometry helpers: quads, homographies, normalized coordinates and rotations."""

from __future__ import annotations

import math

import numpy as np

Quad = list[list[float]]  # [[x,y]*4] tl, tr, br, bl


def apply_h(H: np.ndarray, pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    ones = np.ones((pts.shape[0], 1))
    p = np.hstack([pts, ones]) @ np.asarray(H, dtype=np.float64).T
    return p[:, :2] / p[:, 2:3]


def order_quad(pts) -> Quad:
    """Order 4 points as tl, tr, br, bl (robust for mildly rotated boxes)."""
    p = np.asarray(pts, dtype=np.float64).reshape(4, 2)
    c = p.mean(axis=0)
    ang = np.arctan2(p[:, 1] - c[1], p[:, 0] - c[0])
    p = p[np.argsort(ang)]  # clockwise in image coords starting from ~ -180deg (left)
    # rotate so first point is the top-left (min x+y)
    i = int(np.argmin(p.sum(axis=1)))
    p = np.roll(p, -i, axis=0)
    # ensure order tl -> tr (second point should have larger x than fourth)
    if p[1][0] < p[3][0]:
        p = np.array([p[0], p[3], p[2], p[1]])
    return p.tolist()


def quad_bbox(q) -> tuple[float, float, float, float]:
    a = np.asarray(q, dtype=np.float64).reshape(-1, 2)
    return float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max())


def quad_metrics(q) -> dict:
    """Baseline angle (deg), length along the text direction and height."""
    a = np.asarray(q, dtype=np.float64).reshape(4, 2)
    top = a[1] - a[0]
    bottom = a[2] - a[3]
    vec = (top + bottom) / 2
    length = float(np.hypot(*vec))
    angle = math.degrees(math.atan2(vec[1], vec[0]))
    h1 = np.linalg.norm(a[3] - a[0])
    h2 = np.linalg.norm(a[2] - a[1])
    height = float((h1 + h2) / 2)
    return {"angle": angle, "length": length, "height": height}


def normalize_quad(q, w: float, h: float) -> Quad:
    return [[float(x) / w, float(y) / h] for x, y in q]


def denormalize_quad(q, w: float, h: float) -> Quad:
    return [[float(x) * w, float(y) * h] for x, y in q]


def rotate_norm_quad(q: Quad, degrees_cw: int) -> Quad:
    """Rotate normalized coordinates when the page is rotated clockwise."""
    d = degrees_cw % 360
    out = []
    for x, y in q:
        if d == 90:
            out.append([1 - y, x])
        elif d == 180:
            out.append([1 - x, 1 - y])
        elif d == 270:
            out.append([y, 1 - x])
        else:
            out.append([x, y])
    if d:
        out = order_quad(out)
    return out


def rotation_h(degrees_cw: int, w: int, h: int) -> np.ndarray:
    """Homography mapping pixel coords of an image to the image rotated CW by 90k."""
    d = degrees_cw % 360
    if d == 0:
        return np.eye(3)
    if d == 90:
        return np.array([[0, -1, h], [1, 0, 0], [0, 0, 1]], dtype=np.float64)
    if d == 180:
        return np.array([[-1, 0, w], [0, -1, h], [0, 0, 1]], dtype=np.float64)
    return np.array([[0, 1, 0], [-1, 0, w], [0, 0, 1]], dtype=np.float64)


def scale_h(sx: float, sy: float | None = None) -> np.ndarray:
    return np.diag([sx, sy if sy is not None else sx, 1.0])


def iou_boxes(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / ua if ua > 0 else 0.0


def overlap_ratio(a, b) -> float:
    """Fraction of box a covered by box b."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    area = max(1e-9, (ax1 - ax0) * (ay1 - ay0))
    return ix * iy / area


def crop_quad(img, quad, pad: float = 0.0):
    """Perspective-crop a (possibly rotated) quad to an upright rectangle.

    pad: extra margin as a fraction of the line height on every side.
    """
    import cv2

    q = np.asarray(quad, dtype=np.float32).reshape(4, 2)
    m = quad_metrics(q)
    if pad:

        ux = (q[1] - q[0] + q[2] - q[3]) / 2
        ux = ux / (np.linalg.norm(ux) + 1e-9)
        uy = (q[3] - q[0] + q[2] - q[1]) / 2
        uy = uy / (np.linalg.norm(uy) + 1e-9)
        d = pad * m["height"]
        signs = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
        q = np.array([q[i] + sx * d * ux + sy * d * uy for i, (sx, sy) in enumerate(signs)], dtype=np.float32)

    w = max(2, int(round(max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3])))))
    h = max(2, int(round(max(np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1])))))
    dst = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    M = cv2.getPerspectiveTransform(q, dst)
    return cv2.warpPerspective(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
