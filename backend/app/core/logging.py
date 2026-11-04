"""Log redaction: never write API keys or document text to logs."""

from __future__ import annotations

import logging
import re

_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)(x-api-key|authorization)(['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+"),
]


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        red = msg
        for p in _PATTERNS:
            red = p.sub(lambda m: (m.group(1) + m.group(2) + "[REDACTED]") if m.lastindex else "[REDACTED]", red)
        if red != msg:
            record.msg, record.args = red, ()
        return True


def install_redaction() -> None:
    f = RedactFilter()
    root = logging.getLogger()
    root.addFilter(f)
    for h in root.handlers:
        h.addFilter(f)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx", "anthropic", "pilgibot"):
        logging.getLogger(name).addFilter(f)
    # the SDK/httpx debug logs can include request bodies (images, text): keep them quiet
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
