from __future__ import annotations

from dataclasses import dataclass, field

from app.models.artwork_record import ArtworkRecord


@dataclass(slots=True)
class ArtworkIndexProgress:
    processed: int
    total: int
    current_file: str = ""


@dataclass(slots=True)
class ArtworkIndexSummary:
    discovered: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    unreadable: int = 0
    database_path: str = ""
    elapsed_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return self.added + self.updated + self.removed


@dataclass(slots=True)
class ArtworkSearchResult:
    records: list[ArtworkRecord]
    total_matches: int
