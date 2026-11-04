# PyInstaller spec for the 필기봇 OCR Studio desktop app.
# Build (from backend/, after `STATIC_EXPORT=1 npm run build` in frontend/ and
# `python scripts/fetch_assets.py models`):
#     pyinstaller pilgibot.spec --noconfirm
# Output: dist/Pilgibot/Pilgibot(.exe)  — a folder app (fast start, no unpacking).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

HERE = Path(SPECPATH).resolve()
FRONTEND_OUT = HERE.parent / "frontend" / "out"
MODELS = HERE / "models_data"
ICON = HERE / "packaging" / "pilgibot.ico"

if not (FRONTEND_OUT / "index.html").exists():
    raise SystemExit("frontend/out missing: run `STATIC_EXPORT=1 npm run build` in frontend/ first")

datas = [(str(FRONTEND_OUT), "frontend_out")]
VERSION_FILE = HERE / "app" / "VERSION"  # written by CI (1.0.<run number>); used for the in-app updater
if VERSION_FILE.exists():
    datas.append((str(VERSION_FILE), "app"))
if MODELS.exists():
    datas.append((str(MODELS), "models_data"))
binaries = []
hiddenimports = collect_submodules("uvicorn") + collect_submodules("app")

for pkg in ("rapidocr", "pymupdf", "onnxruntime", "anthropic"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["app/desktop.py"],
    pathex=[str(HERE)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "boto3", "botocore", "s3transfer", "psycopg", "psycopg_binary", "pytest",
              "matplotlib", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Pilgibot",
    console=True,  # small window: shows the address; closing it quits the app
    icon=str(ICON) if ICON.exists() and sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Pilgibot")
