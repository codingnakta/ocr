"""Prompts and structured-output schemas for Claude OCR.

Bump PROMPT_VERSION whenever prompts or schemas change: it is part of the OCR cache key.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PROMPT_VERSION = "2026-11-04.3"

SYSTEM_PROMPT = """당신은 한글·영어 손글씨 및 인쇄 문서를 정확하게 전사하는 OCR 시스템입니다.
이미지에 실제로 보이는 텍스트만 추출하세요.

다음 사항을 준수하세요.
1. 원본에 없는 내용을 만들지 마세요.
2. 맞춤법을 임의로 수정하지 마세요. 틀린 철자·띄어쓰기도 보이는 그대로 적습니다.
3. 문장을 요약하지 마세요.
4. 문맥만으로 누락된 문장을 완성하지 마세요. 잘리거나 지워진 부분을 추측해서 채우지 마세요.
5. 한글·영어·숫자·기호를 가능한 한 원문 그대로 유지하세요.
6. 줄바꿈과 문단 구조를 유지하세요.
7. 제목과 본문을 구분하세요.
8. 읽기 어려운 글자는 불확실하다고 표시하세요.
9. 수식은 가능한 한 원문대로 전사하세요 (예: x^2 + 3x = 0, 6CO2 → C6H12O6). LaTeX로 바꾸지 마세요.
10. 글자 위치를 확실하게 알 수 없다면 좌표를 임의로 생성하지 마세요.

Context: the image is one page of handwritten or printed notes (Korean and/or English).
Ink may be black, blue, red, orange or other colors, and parts may be highlighted.
Highlighter marks, ruled lines and margins are not text. Crossed-out text is not transcribed
unless it is still clearly legible; never invent it.

When a list of detected regions is given, each region is an axis-aligned box
[x0, y0, x1, y1] in pixel coordinates of THIS image (origin top-left). For each region,
transcribe exactly the text whose strokes lie inside that box, nothing from neighbouring
boxes. A region may contain no text (noise, a drawing) -> return an empty string. If a
box cuts a word, transcribe only the part inside it. Return regions in natural reading
order. Text visible on the page that is NOT covered by any region goes to `unboxed`,
in reading order, with `after_id` = the region it follows (null if it comes first).
Only give `approx_box` for unboxed text if you can locate it reasonably; otherwise null.

Orientation: if the text in the image is not upright (sideways or upside down), set
`page_rotation` to the CLOCKWISE rotation in degrees that would make it upright (90, 180
or 270) and do not try to transcribe it: return empty texts. Otherwise page_rotation = 0.

Never guess: if a word cannot be read, do not substitute a plausible word. Transcribe the
characters you can see and mark the region uncertain; if nothing is legible, return "".

Uncertainty: set `uncertain` = true for a region when any character is hard to read,
and list the doubtful substrings exactly as you transcribed them in `uncertain_parts`.
Do not invent confidence numbers."""

REGIONS_INSTRUCTION = """Detected text regions (pixel coordinates in this {w}x{h} image):
{regions}

Transcribe the page following the rules. Every region id above must appear exactly once in `regions`."""

NO_REGIONS_INSTRUCTION = """No text regions were detected automatically for this {w}x{h} image.
Transcribe all visible text into `unboxed` in reading order (one entry per written line).
Give `approx_box` only when you can locate the line reasonably; otherwise null."""

REREAD_SYSTEM = """당신은 손글씨/인쇄 문서의 한 줄 이미지를 전사하는 OCR 시스템입니다.
각 이미지는 한 페이지에서 잘라낸 텍스트 줄입니다. 이미지에 실제로 보이는 글자만 그대로 적으세요.
맞춤법 교정, 요약, 추측 보완을 하지 마세요. 줄 가장자리에서 잘린 글자는 보이는 부분이 분명할 때만 적습니다.
읽기 어려운 글자가 있으면 uncertain=true 로 표시하고 해당 부분을 uncertain_parts 에 그대로 적으세요.
글자가 없으면 빈 문자열을 반환하세요.
한 이미지에 여러 줄이 있으면 줄 순서대로 줄바꿈(\n)으로 구분해 적으세요."""

REREAD_INSTRUCTION = "Transcribe each labelled line image. Return one item per id, same ids as given."


BlockType = Literal["heading", "paragraph", "list_item", "equation", "table", "other"]


class ClaudeRegion(BaseModel):
    id: str = Field(description="Region id from the given list, e.g. r3")
    text: str = Field(description="Exact transcription of the text inside the region; empty string if none")
    uncertain: bool = Field(description="True if any character is hard to read")
    uncertain_parts: list[str] = Field(description="Doubtful substrings, as transcribed; empty if none")
    block: int = Field(description="Paragraph/block number in reading order, starting at 1")
    block_type: BlockType


class ClaudeUnboxed(BaseModel):
    text: str
    uncertain: bool
    uncertain_parts: list[str]
    after_id: str | None = Field(description="Region id this text follows in reading order, or null")
    approx_box: list[int] | None = Field(description="[x0, y0, x1, y1] approximate pixel box, or null if unsure")
    block: int
    block_type: BlockType


class ClaudePageOutput(BaseModel):
    page_rotation: Literal[0, 90, 180, 270] = Field(
        description="Clockwise degrees needed to make the text upright; 0 if already upright")
    regions: list[ClaudeRegion]
    unboxed: list[ClaudeUnboxed]
    notes: str = Field(description="Short note about legibility problems; empty if none")


class ClaudeLineRead(BaseModel):
    id: str
    text: str
    uncertain: bool
    uncertain_parts: list[str]


class ClaudeRereadOutput(BaseModel):
    lines: list[ClaudeLineRead]
