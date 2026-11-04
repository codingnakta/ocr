"""Self-update for the Windows desktop app.

Checks the latest GitHub Release of this repository and, for the installed (Inno Setup)
variant, downloads the new installer, verifies it against the release's SHA256SUMS.txt,
runs it silently and restarts the program. The portable zip and source checkouts only
get a download link (they cannot replace their own files safely).

Flow of an install (Windows only):
  1. download Pilgibot-Setup.exe to <data dir>/updates/ (progress visible in status())
  2. verify size + SHA-256 when the release ships SHA256SUMS.txt
  3. start a detached cmd script that waits for this process to exit, runs the installer
     with /VERYSILENT and starts the new Pilgibot.exe (without opening another browser tab)
  4. exit this process; the open browser tab polls /api/health and reloads when the new
     version answers
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from app.version import APP_VERSION, parse_version

log = logging.getLogger("pilgibot.updater")

REPO = "codingnakta/ocr"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
INSTALLER_NAME = "Pilgibot-Setup.exe"
PORTABLE_NAME = "Pilgibot-Windows-portable.zip"
SUMS_NAME = "SHA256SUMS.txt"
CACHE_TTL_S = 600
MAX_INSTALLER_BYTES = 2 * 1024 * 1024 * 1024

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "info": None}
_state: dict = {"status": "idle", "downloaded": 0, "total": 0, "version": None, "error": None}
BUSY = ("downloading", "installing", "restarting")


def install_type() -> str:
    """installer (Inno Setup, can self-update) | portable (zip) | source (dev checkout)."""
    if not getattr(sys, "frozen", False):
        return "source"
    exe_dir = Path(sys.executable).parent
    return "installer" if any(exe_dir.glob("unins*.exe")) else "portable"


def _open(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": f"pilgibot-ocr-studio/{APP_VERSION}",
                                               "Accept": "application/vnd.github+json, */*"})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - fixed https host


def _fetch_json(url: str) -> dict:
    with _open(url, 10) as r:
        return json.loads(r.read().decode("utf-8"))


def _fetch_text(url: str) -> str:
    with _open(url, 10) as r:
        return r.read(1_000_000).decode("utf-8", "replace")


def check(refresh: bool = False) -> dict:
    """Latest release vs. running version (cached for 10 minutes)."""
    with _lock:
        cached = _cache["info"]
        if cached and not refresh and time.time() - _cache["at"] < CACHE_TTL_S:
            return dict(cached)
    info = {
        "current": APP_VERSION, "install_type": install_type(), "latest": None, "available": False,
        "can_self_update": False, "notes": None, "url": RELEASES_PAGE, "published_at": None,
        "installer": None, "sums_url": None, "portable_url": None, "error": None,
    }
    try:
        rel = _fetch_json(API_LATEST)
        assets = {a.get("name"): a for a in rel.get("assets", []) if a.get("name")}
        latest = parse_version(rel.get("tag_name"))
        current = parse_version(APP_VERSION)
        inst = assets.get(INSTALLER_NAME)
        info.update({
            "latest": ".".join(map(str, latest)) if latest else rel.get("tag_name"),
            "notes": (rel.get("body") or "")[:4000] or None,
            "url": rel.get("html_url") or RELEASES_PAGE,
            "published_at": rel.get("published_at"),
            "installer": {"url": inst["browser_download_url"], "size": int(inst.get("size") or 0)} if inst else None,
            "sums_url": assets[SUMS_NAME]["browser_download_url"] if SUMS_NAME in assets else None,
            "portable_url": assets[PORTABLE_NAME]["browser_download_url"] if PORTABLE_NAME in assets else None,
        })
        info["available"] = bool(latest and current and latest > current)
        info["can_self_update"] = bool(info["available"] and inst and info["install_type"] == "installer"
                                       and sys.platform == "win32")
    except Exception as e:  # offline, rate limited, GitHub down
        log.warning("update check failed: %s", e)
        info["error"] = "업데이트 정보를 가져오지 못했습니다. 인터넷 연결을 확인하세요."
    with _lock:
        _cache.update(at=time.time(), info=dict(info))
    return info


def status() -> dict:
    with _lock:
        return dict(_state)


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def updates_dir() -> Path:
    from app.desktop import data_dir

    d = data_dir() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def start_install(info: dict) -> dict:
    """Begin a self-update in the background; returns the current state."""
    with _lock:
        if _state["status"] in BUSY:
            return dict(_state)
        _state.update(status="downloading", downloaded=0, total=info["installer"]["size"],
                      version=info["latest"], error=None)
    threading.Thread(target=_run_update, args=(info,), name="pilgibot-updater", daemon=True).start()
    return status()


def _run_update(info: dict) -> None:
    try:
        dest_dir = updates_dir()
        for old in dest_dir.glob("*"):
            try:
                old.unlink()
            except OSError:
                pass
        target = dest_dir / f"Pilgibot-Setup-{info['latest']}.exe"
        _download(info["installer"]["url"], target, info["installer"]["size"])
        expected = _expected_sha256(info.get("sums_url"))
        if expected:
            actual = _sha256(target)
            if actual != expected:
                target.unlink(missing_ok=True)
                raise RuntimeError("내려받은 설치 파일의 체크섬이 맞지 않습니다. 잠시 후 다시 시도하세요.")
        else:
            log.warning("release has no %s; installing without checksum verification", SUMS_NAME)
        _set(status="installing")
        _launch_installer(target, dest_dir)
        _set(status="restarting")
        log.info("update to %s started; exiting for the installer", info["latest"])
        threading.Timer(1.5, lambda: os._exit(0)).start()
    except Exception as e:
        log.exception("update failed")
        _set(status="error", error=str(e) or type(e).__name__)


def _download(url: str, target: Path, expected_size: int) -> None:
    tmp = target.with_suffix(".part")
    done = 0
    with _open(url, 30) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or expected_size or 0)
        if total > MAX_INSTALLER_BYTES:
            raise RuntimeError("설치 파일이 비정상적으로 큽니다.")
        _set(total=total)
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if done > MAX_INSTALLER_BYTES:
                raise RuntimeError("설치 파일이 비정상적으로 큽니다.")
            _set(downloaded=done)
    if expected_size and done != expected_size:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("설치 파일을 끝까지 내려받지 못했습니다. 다시 시도하세요.")
    os.replace(tmp, target)


def _expected_sha256(sums_url: str | None) -> str | None:
    if not sums_url:
        return None
    try:
        text = _fetch_text(sums_url)
    except Exception as e:
        raise RuntimeError("체크섬 파일을 내려받지 못했습니다. 다시 시도하세요.") from e
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == INSTALLER_NAME:
            return parts[0].lower()
    return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def update_script(installer: Path, exe: Path, pid: int, log_file: Path) -> str:
    """cmd script: wait until this process is gone, install silently, start the new exe."""
    exe_name = exe.name
    return "\r\n".join([
        "@echo off",
        ":wait",
        f'tasklist /FI "PID eq {pid}" | find /I "{exe_name}" >nul',
        "if not errorlevel 1 (",
        "  ping -n 2 127.0.0.1 >nul",
        "  goto wait",
        ")",
        f'"{installer}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /CLOSEAPPLICATIONS /NOCANCEL /LOG="{log_file}"',
        "set PILGIBOT_NO_BROWSER=1",
        f'start "" "{exe}"',
        "",
    ])


def _launch_installer(installer: Path, work_dir: Path) -> None:
    if sys.platform != "win32":
        raise RuntimeError("자동 업데이트는 Windows 설치형에서만 지원합니다.")
    exe = Path(sys.executable)
    script = work_dir / "update.cmd"
    script.write_text(update_script(installer, exe, os.getpid(), work_dir / "install.log"), encoding="utf-8")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=flags, close_fds=True, cwd=str(work_dir),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
