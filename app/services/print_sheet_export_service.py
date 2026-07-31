from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps


@dataclass(frozen=True)
class PrintLayoutPreset:
    card_width_px: int = 1600
    card_height_px: int = 2400
    columns: int = 3
    rows: int = 3
    gap_px: int = 0
    margin_px: int = 0
    dpi: int = 300
    mirror_backs_horizontally: bool = True

    @property
    def cards_per_sheet(self) -> int:
        return self.columns * self.rows

    @property
    def sheet_width_px(self) -> int:
        return (
            self.margin_px * 2
            + self.columns * self.card_width_px
            + max(0, self.columns - 1) * self.gap_px
        )

    @property
    def sheet_height_px(self) -> int:
        return (
            self.margin_px * 2
            + self.rows * self.card_height_px
            + max(0, self.rows - 1) * self.gap_px
        )


class PrintSheetExportService:
    def expand_cards(self, items: list[dict], processed_root: Path) -> list[dict]:
        cards = []
        for item in items:
            front = processed_root / item["relative_path"]
            back_relative = item.get("effective_back_relative_path")
            if not back_relative:
                raise ValueError(f"Missing back for {item['filename']}")
            back = processed_root / back_relative
            if not front.is_file():
                raise FileNotFoundError(front)
            if not back.is_file():
                raise FileNotFoundError(back)
            for _ in range(int(item["quantity"])):
                cards.append({"front": front, "back": back})
        return cards

    @staticmethod
    def _fit_card(path: Path, width: int, height: int) -> Image.Image:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            return ImageOps.fit(
                image,
                (width, height),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )

    def _make_sheet(
        self,
        cards: list[dict],
        side: str,
        preset: PrintLayoutPreset,
    ) -> Image.Image:
        sheet = Image.new(
            "RGB",
            (preset.sheet_width_px, preset.sheet_height_px),
            "white",
        )
        for slot, card in enumerate(cards):
            logical_column = slot % preset.columns
            row = slot // preset.columns
            column = logical_column
            if side == "back" and preset.mirror_backs_horizontally:
                column = preset.columns - 1 - logical_column
            x = preset.margin_px + column * (preset.card_width_px + preset.gap_px)
            y = preset.margin_px + row * (preset.card_height_px + preset.gap_px)
            card_image = self._fit_card(
                card[side],
                preset.card_width_px,
                preset.card_height_px,
            )
            sheet.paste(card_image, (x, y))
        return sheet

    def export_png_sheets(
        self,
        items: list[dict],
        processed_root: Path,
        output_folder: Path,
        preset: PrintLayoutPreset,
        project_name: str,
        progress_callback=None,
    ) -> dict:
        cards = self.expand_cards(items, processed_root)
        output_folder.mkdir(parents=True, exist_ok=True)
        sheet_count = math.ceil(len(cards) / preset.cards_per_sheet)
        written = []
        for page_index in range(sheet_count):
            page_cards = cards[
                page_index * preset.cards_per_sheet:
                (page_index + 1) * preset.cards_per_sheet
            ]
            for side in ("front", "back"):
                sheet = self._make_sheet(page_cards, side, preset)
                path = output_folder / (
                    f"{project_name}_{side}_{page_index + 1:03d}.png"
                )
                sheet.save(path, format="PNG", dpi=(preset.dpi, preset.dpi))
                written.append(path)
            if progress_callback:
                progress_callback(page_index + 1, sheet_count)
        return {
            "total_cards": len(cards),
            "sheet_count": sheet_count,
            "unfilled_slots": sheet_count * preset.cards_per_sheet - len(cards),
            "files": written,
        }

    def export_pdf(
        self,
        items: list[dict],
        processed_root: Path,
        output_path: Path,
        preset: PrintLayoutPreset,
        project_name: str,
        progress_callback=None,
    ) -> dict:
        """
        Export alternating front/back pages as a duplex-ready PDF.

        ReportLab is used when it is installed. Otherwise LegendForge falls
        back to Pillow's built-in multipage PDF writer, so PDF export does not
        require an additional package and the application can always start.
        """
        cards = self.expand_cards(items, processed_root)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sheet_count = math.ceil(len(cards) / preset.cards_per_sheet)

        try:
            from reportlab.lib.utils import ImageReader
            from reportlab.pdfgen import canvas
        except ModuleNotFoundError:
            pages = []
            try:
                for page_index in range(sheet_count):
                    page_cards = cards[
                        page_index * preset.cards_per_sheet:
                        (page_index + 1) * preset.cards_per_sheet
                    ]
                    pages.append(self._make_sheet(page_cards, "front", preset))
                    pages.append(self._make_sheet(page_cards, "back", preset))
                    if progress_callback:
                        progress_callback(page_index + 1, sheet_count)

                if not pages:
                    raise ValueError("The print project contains no cards.")

                first_page, remaining_pages = pages[0], pages[1:]
                first_page.save(
                    output_path,
                    format="PDF",
                    save_all=True,
                    append_images=remaining_pages,
                    resolution=float(preset.dpi),
                    title=project_name,
                )
            finally:
                for page in pages:
                    page.close()
        else:
            points_per_pixel = 72.0 / preset.dpi
            page_width = preset.sheet_width_px * points_per_pixel
            page_height = preset.sheet_height_px * points_per_pixel
            pdf = canvas.Canvas(
                str(output_path),
                pagesize=(page_width, page_height),
            )

            for page_index in range(sheet_count):
                page_cards = cards[
                    page_index * preset.cards_per_sheet:
                    (page_index + 1) * preset.cards_per_sheet
                ]
                for side in ("front", "back"):
                    for slot, card in enumerate(page_cards):
                        logical_column = slot % preset.columns
                        row = slot // preset.columns
                        column = logical_column
                        if (
                            side == "back"
                            and preset.mirror_backs_horizontally
                        ):
                            column = preset.columns - 1 - logical_column

                        x_px = preset.margin_px + column * (
                            preset.card_width_px + preset.gap_px
                        )
                        y_px_top = preset.margin_px + row * (
                            preset.card_height_px + preset.gap_px
                        )
                        x = x_px * points_per_pixel
                        y = page_height - (
                            y_px_top + preset.card_height_px
                        ) * points_per_pixel

                        fitted = self._fit_card(
                            card[side],
                            preset.card_width_px,
                            preset.card_height_px,
                        )
                        try:
                            pdf.drawImage(
                                ImageReader(fitted),
                                x,
                                y,
                                width=(
                                    preset.card_width_px
                                    * points_per_pixel
                                ),
                                height=(
                                    preset.card_height_px
                                    * points_per_pixel
                                ),
                                preserveAspectRatio=False,
                                mask="auto",
                            )
                        finally:
                            fitted.close()

                    pdf.setTitle(
                        f"{project_name} - "
                        f"{side.title()} sheet {page_index + 1}"
                    )
                    pdf.showPage()

                if progress_callback:
                    progress_callback(page_index + 1, sheet_count)

            pdf.save()

        return {
            "total_cards": len(cards),
            "sheet_count": sheet_count,
            "pdf_pages": sheet_count * 2,
            "unfilled_slots": (
                sheet_count * preset.cards_per_sheet - len(cards)
            ),
            "files": [output_path],
        }
