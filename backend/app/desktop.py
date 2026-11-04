"""필기봇 OCR Studio — desktop launcher (Windows exe, also works on macOS/Linux).

Double-click -> starts the local server on 127.0.0.1 and opens the browser.
Everything (database, uploads, results, API key) is stored in the user's own data
folder; nothing is reachable from other computers.

Environment overrides (optional):
  PILGIBOT_PORT        preferred port (default 8765)
  PILGIBOT_DATA_DIR    data folder
  PILGIBOT_NO_BROWSER  1 = do not open a browser (tests)
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "Pilgibot"


def bundle_dir() -> Path:
    """Folder holding bundled resources (PyInstaller) or the source tree."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]  # backend/


def data_dir() -> Path:
    if os.environ.get("PILGIBOT_DATA_DIR"):
        return Path(os.environ["PILGIBOT_DATA_DIR"])
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME.lower()


def configure_environment() -> Path:
    base = bundle_dir()
    data = data_dir()
    (data / "files").mkdir(parents=True, exist_ok=True)
    (data / "work").mkdir(parents=True, exist_ok=True)
    frontend = base / "frontend_out"
    if not frontend.is_dir():  # running from source: use the repo's static export
        frontend = base.parent / "frontend" / "out"
    defaults = {
        "APP_ENV": "development",
        "AUTH_MODE": "none",
        "DESKTOP_MODE": "true",
        "EMBEDDED_WORKER": "true",
        "DATABASE_URL": f"sqlite:///{(data / 'pilgibot.db').as_posix()}",
        "STORAGE_DIR": str(data / "files"),
        "WORK_DIR": str(data / "work"),
        "FRONTEND_DIR": str(frontend),
        "LOCAL_OCR_MODEL_DIR": str(base / "models_data"),
        "PILGIBOT_CONFIG_FILE": str(data / "config.json"),
        "RETENTION_DAYS": "0",
    }
    for k, v in defaults.items():
        os.environ.setdefault(k, v)
    cfg = data / "config.json"
    if cfg.exists() and not os.environ.get("ANTHROPIC_API_KEY"):
        try:
            key = json.loads(cfg.read_text(encoding="utf-8")).get("anthropic_api_key")
            if key:
                os.environ["ANTHROPIC_API_KEY"] = key
        except ValueError:
            pass
    return data


# local checks must never go through a system/corporate proxy (Windows reads it from the registry)
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_our_server(port: int) -> bool:
    try:
        with _LOCAL.open(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            return r.status == 200 and b'"ok"' in r.read()
    except Exception:
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if sys.platform != "win32":
            # allow re-binding right after a restart (TIME_WAIT), as uvicorn does
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def pick_port(preferred: int) -> tuple[int, bool]:
    """(port, already_running)."""
    for p in [preferred] + list(range(preferred + 1, preferred + 30)):
        if is_our_server(p):
            return p, True
        if port_free(p):
            return p, False
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1], False


def say(msg: str) -> None:
    try:
        print(msg, flush=True)
    except Exception:
        pass


def main() -> None:
    if sys.platform == "win32":
        try:  # show Korean correctly in the console window
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleTitleW("필기봇 OCR Studio")
        except Exception:
            pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass
    data = configure_environment()
    port, running = pick_port(int(os.environ.get("PILGIBOT_PORT", "8765")))
    url = f"http://127.0.0.1:{port}/"
    open_browser = os.environ.get("PILGIBOT_NO_BROWSER") != "1"
    if running:
        say(f"필기봇이 이미 실행 중입니다: {url}")
        if open_browser:
            webbrowser.open(url)
        time.sleep(2)
        return

    import uvicorn

    from app.main import create_app

    say("=" * 60)
    say("  필기봇 OCR Studio")
    say(f"  주소: {url}")
    say(f"  데이터 폴더: {data}")
    say("  이 창을 닫으면 프로그램이 종료됩니다.")
    say("=" * 60)

    def opener():
        for _ in range(120):
            if is_our_server(port):
                if open_browser:
                    webbrowser.open(url)
                say("준비 완료 — 브라우저에서 사용하세요.")
                return
            time.sleep(0.5)

    threading.Thread(target=opener, daemon=True).start()
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning", access_log=False)
    uvicorn.Server(config).run()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
