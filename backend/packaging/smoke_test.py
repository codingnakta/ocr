"""Smoke test for a running desktop build (standard library only).

    python smoke_test.py <base_url> <image> [--mode local|hybrid]

Checks: health, settings, upload, OCR job, searchable PDF export + automatic layer
verification, download. Exits non-zero on failure.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
import uuid
from pathlib import Path

H = {"X-PB-CSRF": "1"}
_OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({})).open  # never use a system proxy


def call(method: str, url: str, body: dict | None = None, raw: bytes | None = None, ctype: str | None = None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = dict(H)
    if body is not None:
        headers["Content-Type"] = "application/json"
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with _OPEN(req, timeout=300) as r:
        payload = r.read()
        return json.loads(payload) if r.headers.get("content-type", "").startswith("application/json") else payload


def multipart(path: Path) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: image/jpeg\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{b}--\r\n".encode()
    return body, f"multipart/form-data; boundary={b}"


def main() -> int:
    try:  # CI consoles on Windows default to cp1252; recognized text is Korean
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass
    base, image = sys.argv[1].rstrip("/"), Path(sys.argv[2])
    mode = sys.argv[sys.argv.index("--mode") + 1] if "--mode" in sys.argv else "local"
    for _ in range(120):
        try:
            if call("GET", f"{base}/api/health").get("ok"):
                break
        except Exception:
            time.sleep(1)
    else:
        print("server did not start")
        return 1
    home = _OPEN(f"{base}/", timeout=30).read()
    assert b"<html" in home.lower(), "frontend not served"
    s = call("GET", f"{base}/api/settings")
    print("settings:", json.dumps({"desktop": s.get("desktop_mode"), "local_ocr": s["local_ocr"],
                                   "claude_configured": s["claude"]["configured"]}, ensure_ascii=False))
    body, ctype = multipart(image)
    doc = call("POST", f"{base}/api/documents", raw=body, ctype=ctype)
    assert doc["page_count"] == 1, doc.get("upload")
    job = call("POST", f"{base}/api/documents/{doc['id']}/ocr", {"mode": mode})
    t0 = time.time()
    while True:
        st = call("GET", f"{base}/api/jobs/{job['id']}")
        if st["status"] not in ("queued", "running"):
            break
        if time.time() - t0 > 600:
            print("OCR timeout")
            return 1
        time.sleep(2)
    print("ocr:", st["status"], f"{time.time() - t0:.1f}s", "cost", st["api"]["cost_usd"])
    assert st["status"] == "completed", st
    ocr = call("GET", f"{base}/api/documents/{doc['id']}/pages/1/ocr")
    print("regions:", len(ocr["regions"]), "| sample:", " / ".join(r["text"] for r in ocr["regions"][:4]))
    assert ocr["regions"], "no text regions detected"
    ex = call("POST", f"{base}/api/documents/{doc['id']}/export", {"format": "pdf"})
    for _ in range(120):
        e = call("GET", f"{base}/api/exports/{ex['id']}")
        if e["status"] in ("done", "failed"):
            break
        time.sleep(1)
    rep = e.get("report", {})
    print("pdf:", e["status"], e.get("size_bytes"), "position_ok", rep.get("position_ok"), "/", rep.get("items"),
          "extract", rep.get("extract_similarity_min"))
    assert e["status"] == "done" and rep.get("position_ok") == rep.get("items")
    pdf = call("GET", f"{base}{e['download_url']}")
    assert isinstance(pdf, bytes) and pdf[:5] == b"%PDF-", "download is not a PDF"
    print("SMOKE TEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
