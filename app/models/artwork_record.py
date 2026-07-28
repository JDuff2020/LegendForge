from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True, slots=True)
class ArtworkRecord:
    id: int | None
    library_kind: str
    relative_path: str
    filename: str
    stem: str
    extension: str
    width: int
    height: int
    file_size: int
    modified_ns: int
    sha256: str
    readable: bool
    indexed_at: str
    pipeline_status: str = "UNKNOWN"

    @property
    def resolution(self) -> str:
        return f"{self.width} × {self.height}" if self.readable else "Unreadable"

    def absolute_path(self, root: Path) -> Path:
        return root / Path(self.relative_path)
