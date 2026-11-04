"""Create images for the browser E2E tests (frontend/e2e). Output dir: argv[1].

Copies real photos from tests/assets/real/ if present and renders one synthetic
Korean handwriting-style page with known text."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from synth import KO_SAMPLE, fonts_available, render_page  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "../frontend/e2e/fixtures")
out.mkdir(parents=True, exist_ok=True)
p = render_page(KO_SAMPLE, family="nanum-pen-script" if fonts_available() else "printed", seed=11,
                perspective=0.04, shading=0.25, noise=3)
p.image.save(out / "02_synthetic_korean.jpg", quality=90)
(out / "02_synthetic_korean.gt.txt").write_text(p.text, encoding="utf-8")
for f in sorted((Path(__file__).parent / "assets" / "real").glob("*.jpg")):
    shutil.copy(f, out / f"01_{f.name}")
print("fixtures in", out.resolve())
