"""Engine-neutral OCR data structures.

All coordinates here are pixel coordinates of the image that was given to the
engine (the preprocessed image). The pipeline maps them back to the page.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

EngineName = Literal["local", "claude", "hybrid"]


@dataclass
class DetectedLine:
    """A text line found by the local detector, optionally with local recognition."""

    key: str  # stable id within the page, e.g. "r3"
    quad: list[list[float]]  # [[x,y]*4] tl,tr,br,bl in processed px
    local_text: str | None = None
    local_score: float | None = None


@dataclass
class RegionResult:
    key: str
    quad: list[list[float]] | None  # None = could not be aligned to a position
    text: str
    engine: str  # local | claude | claude_region
    position_source: str = "local_det"  # local_det | local_det_refined | none
    text_claude: str | None = None
    text_local: str | None = None
    local_score: float | None = None
    uncertain: bool = False
    flags: list[str] = field(default_factory=list)
    block_index: int = 0
    block_type: str = "paragraph"


@dataclass
class PageOcrResult:
    engine: str
    model: str | None
    regions: list[RegionResult]
    warnings: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)  # raw engine payloads (Claude JSON etc.)
    api_calls: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PageOcrResult":
        regs = [RegionResult(**r) for r in d.get("regions", [])]
        return cls(
            engine=d["engine"],
            model=d.get("model"),
            regions=regs,
            warnings=list(d.get("warnings", [])),
            raw=d.get("raw", {}),
            api_calls=d.get("api_calls", 0),
        )


class OcrError(Exception):
    """Recoverable OCR failure for one page (the job continues with other pages)."""


class CostLimitExceeded(Exception):
    pass
