from __future__ import annotations
from pathlib import Path
import math
from openpyxl import load_workbook
from app.models.results import PricingSummary

class PricingFormatError(ValueError):
    pass

class PricingService:
    """Parses the user's repeated 3-column MPC layout: Card Amount / Number Decks / Cost."""

    def load(self, path: Path) -> PricingSummary:
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                parsed = self._parse_sheet(ws)
                if parsed.deck_sizes:
                    parsed.source_sheet = ws.title
                    return parsed
        finally:
            wb.close()
        raise PricingFormatError("No repeated Card Amount / Number Decks / Cost blocks were found.")

    def _parse_sheet(self, ws) -> PricingSummary:
        max_col = ws.max_column
        prices: dict[int, dict[str, float]] = {}
        tiers: list[str] = []
        deck_sizes: list[int] = []
        for col in range(1, max_col + 1, 3):
            headers = [str(ws.cell(1, c).value or "").strip().lower() for c in range(col, min(col + 3, max_col + 1))]
            if headers != ["card amount", "number decks", "cost"]:
                continue
            size_value = ws.cell(2, col).value
            try:
                size = int(size_value)
            except (TypeError, ValueError):
                continue
            deck_sizes.append(size)
            prices[size] = {}
            for row in range(2, ws.max_row + 1):
                tier = str(ws.cell(row, col + 1).value or "").strip()
                cost = ws.cell(row, col + 2).value
                if not tier or cost is None:
                    continue
                try:
                    prices[size][tier] = float(cost)
                except (TypeError, ValueError):
                    raise PricingFormatError(f"Invalid cost at {ws.title}!{ws.cell(row, col + 2).coordinate}")
                if tier not in tiers:
                    tiers.append(tier)
        return PricingSummary(deck_sizes=deck_sizes, quantity_tiers=tiers, prices=prices)


    @staticmethod
    def tier_for_decks(number_decks: int, tiers: list[str]) -> str | None:
        """Return the workbook tier label containing number_decks."""
        for tier in tiers:
            text = str(tier).strip()
            if not text:
                continue
            if text.endswith("+"):
                try:
                    if number_decks >= int(text[:-1]):
                        return tier
                except ValueError:
                    continue
            elif "-" in text:
                low_text, high_text = text.split("-", 1)
                try:
                    low = int(low_text)
                    high = int(high_text)
                except ValueError:
                    continue
                if low <= number_decks <= high:
                    return tier
        return None

    def quote_options(self, summary: PricingSummary, total_cards: int) -> list[dict]:
        """
        Compare all MPC deck capacities.

        The estimate assumes cards are packed across decks of one selected
        capacity. The workbook price is treated as a per-deck price for the
        resulting number-of-decks tier.
        """
        if total_cards < 1:
            raise ValueError("Total cards must be at least 1.")

        options = []
        for capacity in sorted(set(summary.deck_sizes), reverse=True):
            deck_count = math.ceil(total_cards / capacity)
            tier = self.tier_for_decks(deck_count, summary.quantity_tiers)
            if tier is None:
                continue
            unit_price = summary.prices.get(capacity, {}).get(tier)
            if unit_price is None:
                continue
            total_price = round(float(unit_price) * deck_count, 2)
            options.append(
                {
                    "deck_capacity": capacity,
                    "deck_count": deck_count,
                    "quantity_tier": tier,
                    "unit_price": round(float(unit_price), 2),
                    "total_price": total_price,
                    "unused_slots": deck_count * capacity - total_cards,
                    "total_cards": total_cards,
                }
            )
        return sorted(
            options,
            key=lambda option: (
                option["total_price"],
                option["unused_slots"],
                option["deck_count"],
            ),
        )

    def best_quote(self, summary: PricingSummary, total_cards: int) -> dict:
        options = self.quote_options(summary, total_cards)
        if not options:
            raise PricingFormatError(
                "The pricing workbook does not contain a usable tier for this card total."
            )
        return options[0]
