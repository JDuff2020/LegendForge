from pathlib import Path

from PIL import Image

from app.services.print_sheet_export_service import (
    PrintLayoutPreset,
    PrintSheetExportService,
)


def make_image(path: Path, size=(100, 150), color="red"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def test_png_sheet_export_pairs_fronts_and_mirrors_backs(tmp_path):
    processed = tmp_path / "processed"
    make_image(processed / "front.png", color="red")
    make_image(processed / "back.png", color="blue")
    items = [
        {
            "filename": "front.png",
            "relative_path": "front.png",
            "effective_back_relative_path": "back.png",
            "quantity": 2,
        }
    ]
    preset = PrintLayoutPreset(
        card_width_px=100,
        card_height_px=150,
        columns=2,
        rows=1,
    )
    result = PrintSheetExportService().export_png_sheets(
        items,
        processed,
        tmp_path / "out",
        preset,
        "Test",
    )
    assert result["total_cards"] == 2
    assert result["sheet_count"] == 1
    assert (tmp_path / "out" / "Test_front_001.png").is_file()
    assert (tmp_path / "out" / "Test_back_001.png").is_file()


def test_pdf_export_creates_front_and_back_pages(tmp_path):
    processed = tmp_path / "processed"
    make_image(processed / "front.png", color="red")
    make_image(processed / "back.png", color="blue")
    items = [
        {
            "filename": "front.png",
            "relative_path": "front.png",
            "effective_back_relative_path": "back.png",
            "quantity": 1,
        }
    ]
    result = PrintSheetExportService().export_pdf(
        items,
        processed,
        tmp_path / "output.pdf",
        PrintLayoutPreset(
            card_width_px=100,
            card_height_px=150,
            columns=1,
            rows=1,
        ),
        "Test",
    )
    assert result["pdf_pages"] == 2
    assert (tmp_path / "output.pdf").stat().st_size > 0
