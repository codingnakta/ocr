"""Application version.

The desktop build writes `app/VERSION` (e.g. `1.0.12`, the GitHub run number) before
packaging; it is bundled next to this file. Source checkouts report `0.0.0-dev`.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str | None) -> tuple[int, int, int] | None:
    """`desktop-v1.0.12` / `1.0.12` -> (1, 0, 12); None when there is no x.y.z in it."""
    m = _VERSION_RE.search(text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _read_version() -> str:
    if os.environ.get("PILGIBOT_VERSION"):
        return os.environ["PILGIBOT_VERSION"].strip()
    candidates = [Path(__file__).with_name("VERSION")]
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.insert(0, base / "app" / "VERSION")
    for c in candidates:
        try:
            if c.is_file():
                v = c.read_text(encoding="utf-8").strip()
                if v:
                    return v
        except OSError:
            pass
    return "0.0.0-dev"


APP_VERSION = _read_version()
