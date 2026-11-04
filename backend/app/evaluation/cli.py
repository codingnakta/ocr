"""Batch OCR evaluation.

    python -m app.evaluation.cli <folder> [--engines local,ai,hybrid] [--out report.json]

<folder> holds images (jpg/png/webp). An optional `<name>.gt.txt` next to an image is
its human-verified ground truth. CER/WER are computed only for images that have one;
for the others only time, cost and line counts are reported. AI/hybrid engines call
the Claude API (cost is printed per image).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from app.evaluation.runner import evaluate_image

EXT = {".jpg", ".jpeg", ".png", ".webp"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--engines", default="local,hybrid")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    engines = [e.strip() for e in a.engines.split(",") if e.strip()]
    images = sorted(p for p in a.folder.iterdir() if p.suffix.lower() in EXT)
    if not images:
        print("no images found", file=sys.stderr)
        return 1
    rows = []
    for img in images:
        gt_file = img.with_suffix("").with_suffix(".gt.txt")
        if not gt_file.exists():
            gt_file = img.parent / (img.stem + ".gt.txt")
        gt = gt_file.read_text(encoding="utf-8") if gt_file.exists() else ""
        res = evaluate_image(img.read_bytes(), gt, engines, None)
        for r in res["results"]:
            row = {"image": img.name, "engine": r["engine"], "has_ground_truth": bool(gt),
                   "seconds": r.get("seconds"), "cost_usd": r.get("cost_usd"), "lines": r.get("lines"),
                   "uncertain_lines": r.get("uncertain_lines"), "unaligned_lines": r.get("unaligned_lines"),
                   "pdf_position_ok": (r.get("pdf") or {}).get("position_ok_ratio"), "error": r.get("error")}
            if r.get("accuracy"):
                row.update(cer=r["accuracy"]["cer"], wer=r["accuracy"]["wer"],
                           pdf_text_cer=r["accuracy"]["pdf_text_cer"], missed_lines=r["accuracy"]["missed_lines"])
            rows.append(row)
            acc = f"CER {row['cer']:.2%} WER {row['wer']:.2%}" if "cer" in row else "정답 없음 (정확도 미측정)"
            print(f"{img.name:30s} {r['engine']:7s} {acc:32s} {row['seconds']}s ${row['cost_usd'] or 0:.4f}")
    print("\n=== summary (images with ground truth only) ===")
    for e in engines:
        rs = [r for r in rows if r["engine"] == e and "cer" in r]
        if rs:
            print(f"{e:7s} n={len(rs)}  mean CER {statistics.mean(r['cer'] for r in rs):.2%}  "
                  f"mean WER {statistics.mean(r['wer'] for r in rs):.2%}  "
                  f"cost/page ${statistics.mean(r['cost_usd'] or 0 for r in rs):.4f}")
        else:
            print(f"{e:7s} no ground truth -> accuracy not measured")
    if a.out:
        a.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print("report:", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
