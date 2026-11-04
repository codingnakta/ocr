"""Export: searchable PDF, TXT, Markdown, JSON — plus automatic PDF text-layer checks.

The PDF check re-opens the generated file and verifies, per page, that the text
written into the layer can be extracted again (Unicode correctness) and that the
text found inside each region's rectangle matches that region (position). This
verifies the file; whether a particular viewer lets you drag-select it is a
separate check (Playwright with PDF.js, and the manual viewer checklist).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pymupdf

from app.evaluation.metrics import normalize, similarity
from app.imaging.geometry import apply_h, denormalize_quad, quad_bbox
from app.pdf.merger import add_image_page, add_pdf_page, existing_text_rects, prepare_image_stream
from app.pdf.text_layer import LayerItem, place_items


@dataclass
class ExportRegion:
    text: str
    quad_norm: list | None  # display-normalized quad or None (unaligned)
    block_index: int = 0
    block_type: str = "paragraph"
    uncertain: bool = False
    meta: dict = field(default_factory=dict)


@dataclass
class ExportPage:
    number: int
    kind: str  # image | pdf
    source_bytes: bytes
    mime: str
    pdf_index: int = 0
    user_rotation: int = 0
    display_w: int = 0
    display_h: int = 0
    content_type: str = "image"
    regions: list[ExportRegion] = field(default_factory=list)
    processed_bytes: bytes | None = None  # for "enhanced" image mode
    processed_H: list | None = None  # display px -> processed px
    label: str = ""


# ------------------------------------------------------------------ PDF --
def _already_present(text: str, quad, existing: list) -> bool:
    """True when the source PDF already has this text at this place (no duplicate layer)."""
    b = pymupdf.Rect(quad_bbox(quad))
    pad = b.height * 0.3
    clip = pymupdf.Rect(b.x0 - pad, b.y0 - pad, b.x1 + pad, b.y1 + pad)
    inside = [(r, w) for r, w in existing if (r & clip).get_area() > 0.5 * max(r.get_area(), 1e-6)]
    if not inside:
        return False
    found = "".join(w for _, w in inside)
    if similarity(text, found) >= 0.6:
        return True
    covered = sum((r & b).get_area() for r, _ in inside)
    return covered > 0.5 * max(b.get_area(), 1e-6)


def build_pdf(pages: list[ExportPage], size_mode: str = "original", image_source: str = "original",
              title: str = "") -> tuple[bytes, dict]:
    out = pymupdf.open()
    report_pages = []
    pdf_cache: dict[int, pymupdf.Document] = {}
    try:
        for p in pages:
            skipped_dupe = 0
            if p.kind == "pdf":
                key = id(p.source_bytes)
                if key not in pdf_cache:
                    pdf_cache[key] = pymupdf.open(stream=p.source_bytes, filetype="pdf")
                page, rect = add_pdf_page(out, pdf_cache[key], p.pdf_index, p.user_rotation)
                region_space = ("display", rect)
            elif image_source == "enhanced" and p.processed_bytes and p.processed_H:
                import io

                from PIL import Image

                im = Image.open(io.BytesIO(p.processed_bytes))
                page, rect = add_image_page(out, p.processed_bytes, 0, im.width, im.height, size_mode)
                region_space = ("processed", rect, im.width, im.height)
            else:
                stream, rot = prepare_image_stream(p.source_bytes, p.mime, p.user_rotation)
                page, rect = add_image_page(out, stream, rot, p.display_w, p.display_h, size_mode)
                region_space = ("display", rect)

            existing = existing_text_rects(page) if p.kind == "pdf" else []
            items: list[LayerItem] = []
            for r in p.regions:
                if r.quad_norm is None or not r.text.strip():
                    continue
                if region_space[0] == "processed":
                    _, rr, pw, ph = region_space
                    px = denormalize_quad(r.quad_norm, p.display_w, p.display_h)
                    q = apply_h(p.processed_H, px)
                    quad = [[rr.x0 + x / pw * rr.width, rr.y0 + y / ph * rr.height] for x, y in q.tolist()]
                else:
                    rr = region_space[1]
                    quad = [[rr.x0 + x * rr.width, rr.y0 + y * rr.height] for x, y in r.quad_norm]
                if existing and _already_present(r.text, quad, existing):
                    skipped_dupe += 1  # text already present in the source PDF
                    continue
                items.append(LayerItem(text=r.text, quad=quad))
            placed = place_items(page, items)
            report_pages.append({"number": p.number, "placed": len(placed), "skipped_existing_text": skipped_dupe,
                                 "items": placed})
        out.set_metadata({"title": title or "OCR", "producer": "필기봇 OCR Studio (PyMuPDF)",
                          "creator": "필기봇 OCR Studio"})
        out.subset_fonts()
        data = out.tobytes(garbage=3, deflate=True, deflate_images=False)
    finally:
        out.close()
        for d in pdf_cache.values():
            d.close()
    return data, verify_pdf(data, report_pages)


def _page_chars(page: pymupdf.Page) -> list[tuple[str, float, float]]:
    """(char, center x, center y) in *display* coordinates."""
    out = []
    mat = page.rotation_matrix
    for b in page.get_text("rawdict")["blocks"]:
        for line in b.get("lines", []):
            for span in line["spans"]:
                for ch in span["chars"]:
                    if ch["c"].isspace():
                        continue
                    r = pymupdf.Rect(ch["bbox"])
                    c = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
                    if page.rotation:
                        c = c * mat
                    out.append((ch["c"], c.x, c.y))
    return out


def verify_pdf(data: bytes, report_pages: list[dict]) -> dict:
    """Re-open the PDF and check extraction (Unicode) and placement (position).

    Unicode: all layer text must come back from extraction unchanged.
    Position: the characters whose centers fall inside a region's rectangle must
    spell that region's text (in order).
    """
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages_out = []
    total_items = 0
    total_pos_ok = 0
    text_sims = []
    try:
        for idx, rp in enumerate(report_pages):
            page = doc[idx]
            expected = "".join(it["text"] for it in rp["items"])
            chars = _page_chars(page)
            got = "".join(c for c, _, _ in chars)
            sim = similarity(expected, got) if expected else 1.0
            pos_ok = 0
            failures = []
            for it in rp["items"]:
                x0, y0, x1, y1 = quad_bbox(it["quad"])
                pad = (y1 - y0) * 0.15
                inside = "".join(c for c, cx, cy in chars
                                 if x0 - pad <= cx <= x1 + pad and y0 - pad <= cy <= y1 + pad)
                want = normalize(it["text"], ignore_space=True)
                if want and (want in inside or similarity(want, inside) >= 0.9):
                    pos_ok += 1
                else:
                    failures.append({"text": it["text"][:60], "found": inside[:60]})
            n = len(rp["items"])
            total_items += n
            total_pos_ok += pos_ok
            if expected:
                text_sims.append(sim)
            pages_out.append({"number": rp["number"], "placed": n, "position_ok": pos_ok,
                              "extract_similarity": round(sim, 4),
                              "skipped_existing_text": rp["skipped_existing_text"],
                              "failures": failures[:5]})
    finally:
        doc.close()
    return {
        "pages": pages_out,
        "items": total_items,
        "position_ok": total_pos_ok,
        "position_ok_ratio": round(total_pos_ok / total_items, 4) if total_items else None,
        "extract_similarity_min": round(min(text_sims), 4) if text_sims else None,
        "note": "파일 자체 검증(유니코드 추출·영역 위치)입니다. 뷰어별 드래그 선택은 별도 확인이 필요합니다.",
    }


# ----------------------------------------------------------------- text --
def _rows(regions: list[ExportRegion]) -> list[list[ExportRegion]]:
    """Group consecutive regions that sit on the same visual row."""
    rows: list[list[ExportRegion]] = []
    cur: list[ExportRegion] = []
    cur_box = None

    def same_row(box, other) -> bool:
        if box is None or other is None:
            return False
        y0, y1 = max(box[1], other[1]), min(box[3], other[3])
        h = min(box[3] - box[1], other[3] - other[1])
        return h > 0 and (y1 - y0) >= 0.5 * h and other[0] >= box[0]

    for i, r in enumerate(regions):
        box = quad_bbox(r.quad_norm) if r.quad_norm else None
        if not cur:
            cur, cur_box = [r], box
            continue
        if box is None:
            nxt = next((x for x in regions[i + 1:] if x.quad_norm), None)
            nbox = quad_bbox(nxt.quad_norm) if nxt else None
            if r.block_index == cur[-1].block_index and same_row(cur_box, nbox):
                cur.append(r)
                continue
            rows.append(cur)
            cur, cur_box = [r], None
            continue
        if r.block_index == cur[-1].block_index and same_row(cur_box, box):
            cur.append(r)
            cur_box = (min(cur_box[0], box[0]), min(cur_box[1], box[1]), max(cur_box[2], box[2]), max(cur_box[3], box[3]))
        else:
            rows.append(cur)
            cur, cur_box = [r], box
    if cur:
        rows.append(cur)
    return rows


def page_lines(p: ExportPage) -> list[tuple[str, int, str]]:
    """(line text, block index, block type) in reading order."""
    out = []
    for row in _rows([r for r in p.regions if r.text.strip()]):
        out.append((" ".join(r.text.strip() for r in row), row[0].block_index, row[0].block_type))
    return out


def build_txt(pages: list[ExportPage]) -> str:
    parts = []
    for p in pages:
        lines, prev_block = [], None
        for text, block, _ in page_lines(p):
            if prev_block is not None and block != prev_block:
                lines.append("")
            lines.append(text)
            prev_block = block
        parts.append(f"=== {p.number} 페이지 ===\n" + "\n".join(lines))
    return "\n\n".join(parts).strip() + "\n"


def build_md(pages: list[ExportPage], title: str) -> str:
    md = [f"# {title}", ""]
    for p in pages:
        md.append(f"## {p.number} 페이지")
        md.append("")
        prev_block = None
        for text, block, btype in page_lines(p):
            if prev_block is not None and block != prev_block:
                md.append("")
            esc = text.replace("\\", "\\\\")
            if btype == "heading":
                md.append(f"### {esc}")
            elif btype == "list_item":
                md.append(esc if esc.lstrip().startswith(("-", "*", "•")) else f"- {esc}")
            elif btype == "equation":
                md.append(f"`{text}`")
            else:
                md.append(esc + "  ")
            prev_block = block
        md.append("")
    return "\n".join(md).strip() + "\n"


def build_json(pages: list[ExportPage], doc_meta: dict) -> str:
    data = {
        **doc_meta,
        "coordinate_system": "polygon = normalized [x,y] (0..1) of the displayed page, top-left origin; "
                             "null = text could not be aligned to a position",
        "pages": [
            {
                "page_number": p.number,
                "label": p.label,
                "width_px": p.display_w,
                "height_px": p.display_h,
                "regions": [{"text": r.text, "polygon": r.quad_norm, "block": r.block_index,
                             "block_type": r.block_type, "uncertain": r.uncertain, **r.meta} for r in p.regions],
            }
            for p in pages
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)
