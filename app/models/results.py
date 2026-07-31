from dataclasses import dataclass, field
from typing import Any

@dataclass
class ArtworkSummary:
    total_images: int = 0
    by_extension: dict[str, int] = field(default_factory=dict)
    readable_images: int = 0
    unreadable_images: int = 0
    average_width: int = 0
    average_height: int = 0
    duplicate_content_groups: int = 0

@dataclass
class PricingSummary:
    deck_sizes: list[int] = field(default_factory=list)
    quantity_tiers: list[str] = field(default_factory=list)
    prices: dict[int, dict[str, float]] = field(default_factory=dict)
    source_sheet: str = ""

@dataclass
class InventorySummary:
    sheets: list[str] = field(default_factory=list)
    total_rows: int = 0
    estimated_cards: int = 0
    categories: dict[str, int] = field(default_factory=dict)
    detected_columns: dict[str, Any] = field(default_factory=dict)

@dataclass
class ValidationItem:
    label: str
    status: str
    message: str

@dataclass
class ValidationReport:
    items: list[ValidationItem]
    artwork: ArtworkSummary | None = None
    pricing: PricingSummary | None = None
    inventory: InventorySummary | None = None

    @property
    def ready(self) -> bool:
        return all(i.status in {"PASS", "OPTIONAL"} for i in self.items)
