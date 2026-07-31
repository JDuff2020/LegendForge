from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProcessingPreset:
    width: int = 744
    height: int = 1039
    output_format: str = "PNG"
    quality: int = 95
    fit_mode: str = "Minimum size (proportional)"
    background: str = "#000000"

    @property
    def extension(self) -> str:
        return {
            "PNG": ".png",
            "JPEG": ".jpg",
            "WEBP": ".webp",
        }.get(self.output_format.upper(), ".png")


@dataclass(frozen=True, slots=True)
class ProcessingItemResult:
    original_id: int
    source_path: Path
    output_path: Path | None
    status: str
    message: str = ""


@dataclass(slots=True)
class ProcessingSummary:
    total: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    results: list[ProcessingItemResult] | None = None

    def __post_init__(self) -> None:
        if self.results is None:
            self.results = []
