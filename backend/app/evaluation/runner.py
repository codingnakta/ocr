"""Compare OCR engines on one image.

Accuracy numbers (CER/WER, missed lines) are reported only when a ground truth is
given. Without it we report raw outputs, time and cost and say explicitly that
accuracy was not measured. The PDF check builds a one-page searchable PDF from each
result and measures how faithfully the text comes back out of the PDF.
"""

from __future__ import annotations

import time

from app.evaluation.metrics import cer, missed_lines, wer
from app.imaging.io import load_oriented, pil_to_bgr
from app.ocr.base import OcrError
from app.ocr.claude_engine import NullLedger
from app.ocr.pipeline import run_page_ocr
from app.pdf.exporter import ExportPage, ExportRegion, build_pdf


def _text(regions) -> str:
    return "\n".join(r.text for r in regions if r.text.strip())


def evaluate_image(data: bytes, ground_truth: str, engines: list[str], owner_id: str | None = None,
                   models: list[str] | None = None) -> dict:
    from app.services.ocr_service import DbLedger

    img = load_oriented(data)
    bgr = pil_to_bgr(img)
    gt = (ground_truth or "").strip()
    results = []
    runs = []
    for name in engines:
        if name == "local":
            runs.append((name, None))
        else:
            runs.extend((name, m) for m in (models or [None]))
    for name, model in runs:
        ledger = DbLedger(owner_id, None, None, None) if owner_id else NullLedger()
        t0 = time.monotonic()
        try:
            out = run_page_ocr(bgr, name, ledger, claude_model=model)
        except OcrError as e:
            results.append({"engine": name, "model": model, "error": str(e)})
            continue
        elapsed = time.monotonic() - t0
        regs = out.result.regions
        hyp = _text(regs)
        page = ExportPage(number=1, kind="image", source_bytes=data, mime="image/jpeg", display_w=img.width,
                          display_h=img.height, regions=[ExportRegion(r.text, r.quad) for r in regs])
        _, report = build_pdf([page])
        placed_text = "\n".join(r.text for r in regs if r.quad is not None and r.text.strip())
        item = {
            "engine": name, "model": out.result.model, "text": hyp, "seconds": round(elapsed, 2),
            "api_calls": out.result.api_calls,
            "cost_usd": round(getattr(ledger, "cost", None) or getattr(ledger, "total_cost", 0.0), 6),
            "lines": len(regs), "uncertain_lines": sum(1 for r in regs if r.uncertain),
            "unaligned_lines": sum(1 for r in regs if r.quad is None and r.text.strip()),
            "pdf": {"position_ok_ratio": report.get("position_ok_ratio"),
                    "extract_similarity_min": report.get("extract_similarity_min")},
            "warnings": out.result.warnings,
            "regions": [{"text": r.text, "uncertain": r.uncertain, "aligned": r.quad is not None,
                         "text_local": r.text_local, "flags": r.flags} for r in regs],
        }
        if gt:
            item["accuracy"] = {
                "cer": round(cer(gt, hyp), 4), "cer_with_spaces": round(cer(gt, hyp, ignore_space=False), 4),
                "wer": round(wer(gt, hyp), 4), "missed_lines": missed_lines(gt, hyp),
                # text recoverable from the PDF layer vs ground truth (unaligned lines are not in the PDF)
                "pdf_text_cer": round(cer(gt, placed_text), 4),
            }
        results.append(item)
    return {
        "ground_truth_provided": bool(gt),
        "note": None if gt else "정답 텍스트가 없어 정확도(CER/WER)는 계산하지 않았습니다.",
        "results": results,
    }
