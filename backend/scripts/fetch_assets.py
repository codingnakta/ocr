"""Download local OCR models and test fonts.

Usage:
    python scripts/fetch_assets.py models      # local OCR models -> models_data/
    python scripts/fetch_assets.py fonts       # handwriting-style test fonts -> tests/assets/fonts/

Models
------
1. Preferred: RapidOCR's official model registry (ModelScope). This gives the
   Korean PP-OCRv5 recognition model (korean_PP-OCRv5_rec_mobile) which is what
   the local / hybrid engine is configured for by default.
2. Fallback (when ModelScope is unreachable): the PyPI wheel `onnxocr` ships the
   PP-OCRv5 multilingual detection/classification/recognition ONNX models
   (Apache-2.0, PaddleOCR). Detection works for Hangul; that recognition model does
   NOT contain Hangul, so Korean local recognition is unavailable in that case.

Fonts (tests only)
------------------
Handwriting-style Korean fonts (SIL OFL) from the npm registry (@fontsource/*).
They are used to synthesize test images. Synthetic "handwriting" is NOT real
handwriting; accuracy on it says little about real notes.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
MODELS = BACKEND / "models_data"
FONTS = BACKEND / "tests" / "assets" / "fonts"

FONT_PACKAGES = ["nanum-pen-script", "gaegu", "gamja-flower", "hi-melody"]


def _get(url: str, timeout: int = 120) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - fixed hosts
        return r.read()


def fetch_models() -> None:
    MODELS.mkdir(parents=True, exist_ok=True)
    ok = False
    try:
        # RapidOCR downloads its configured models on first construction.
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        eng = RapidOCR(
            params={
                "Global.model_root_dir": str(MODELS / "rapidocr"),
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
                "Rec.lang_type": LangRec.KOREAN,
            }
        )
        # RapidOCR loads models lazily; force the Korean recognizer download now.
        eng._load_rec_model()
        print("RapidOCR Korean recognition model ready in", MODELS / "rapidocr")
        ok = True
    except Exception as e:  # network policy, offline, ...
        print("RapidOCR model download failed:", type(e).__name__, str(e)[:200])

    target = MODELS / "onnxocr"
    if not (target / "det.onnx").exists():
        print("Fetching fallback models from PyPI wheel 'onnxocr' ...")
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(
                [sys.executable, "-m", "pip", "download", "onnxocr==4.0.0", "--no-deps", "-d", td, "-q"],
                check=True,
            )
            whl = next(Path(td).glob("onnxocr-*.whl"))
            target.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(whl) as z:
                for src, dst in {
                    "onnxocr/models/ppocrv5/det/det.onnx": "det.onnx",
                    "onnxocr/models/ppocrv5/cls/cls.onnx": "cls.onnx",
                    "onnxocr/models/ppocrv5/rec/rec.onnx": "rec_multilingual.onnx",
                    "onnxocr/models/ppocrv5/ppocrv5_dict.txt": "ppocrv5_dict.txt",
                    "onnxocr/models/orientation/rapid_orientation.onnx": "rapid_orientation.onnx",
                }.items():
                    with z.open(src) as f, open(target / dst, "wb") as out:
                        shutil.copyfileobj(f, out)
        (target / "SOURCE.txt").write_text(
            "Extracted from PyPI onnxocr==4.0.0 (PaddleOCR PP-OCRv5 models, Apache-2.0).\n"
            "rec_multilingual.onnx covers Chinese/English/Japanese, NOT Korean.\n",
            encoding="utf-8",
        )
        print("Fallback models at", target)
    if not ok:
        print("NOTE: Korean local recognition model unavailable; local engine will use the "
              "multilingual fallback (no Hangul). Claude/hybrid modes still work.")


def fetch_fonts() -> None:
    FONTS.mkdir(parents=True, exist_ok=True)
    for pkg in FONT_PACKAGES:
        dst = FONTS / pkg
        if dst.exists() and any(dst.glob("*.woff")):
            continue
        meta = json.loads(_get(f"https://registry.npmjs.org/@fontsource/{pkg}/latest"))
        tgz = _get(meta["dist"]["tarball"])
        dst.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(tgz)) as tf:
            for m in tf.getmembers():
                name = Path(m.name).name
                if m.isfile() and name.endswith("-400-normal.woff"):
                    f = tf.extractfile(m)
                    if f:
                        (dst / name).write_bytes(f.read())
                if m.isfile() and name == "LICENSE":
                    f = tf.extractfile(m)
                    if f:
                        (dst / "LICENSE").write_bytes(f.read())
        print("font", pkg, "->", dst)


if __name__ == "__main__":
    what = sys.argv[1:] or ["models", "fonts"]
    if "models" in what:
        fetch_models()
    if "fonts" in what:
        fetch_fonts()
