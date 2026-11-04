"""Generate PWA icons (run once: python scripts/make_icons.py). Requires Pillow."""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "public" / "icons"
BLUE = (59, 79, 196)


def icon(size: int, maskable: bool = False) -> Image.Image:
    img = Image.new("RGB", (size, size), BLUE if maskable else (248, 249, 251))
    d = ImageDraw.Draw(img)
    pad = int(size * (0.18 if maskable else 0.06))
    if not maskable:
        d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=int(size * 0.2), fill=BLUE)
    # page
    s = size
    px0, py0, px1, py1 = int(s * 0.30), int(s * 0.22), int(s * 0.70), int(s * 0.78)
    d.rounded_rectangle([px0, py0, px1, py1], radius=int(s * 0.04), fill=(255, 255, 255))
    # handwriting lines
    for i, w in enumerate((0.30, 0.24, 0.28)):
        y = int(s * (0.36 + i * 0.11))
        d.line([(int(s * 0.36), y), (int(s * (0.36 + w)), y - int(s * 0.015))], fill=BLUE, width=max(2, s // 40))
    # search lens
    cx, cy, r = int(s * 0.66), int(s * 0.70), int(s * 0.11)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 196, 0), width=max(3, s // 28))
    d.line([(cx + int(r * 0.7), cy + int(r * 0.7)), (cx + int(r * 1.6), cy + int(r * 1.6))], fill=(255, 196, 0), width=max(3, s // 24))
    return img


OUT.mkdir(parents=True, exist_ok=True)
icon(192).save(OUT / "icon-192.png")
icon(512).save(OUT / "icon-512.png")
icon(512, maskable=True).save(OUT / "icon-maskable-512.png")
icon(180).save(OUT / "apple-touch-icon.png")
print("icons written to", OUT)
