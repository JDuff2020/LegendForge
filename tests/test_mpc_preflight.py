from pathlib import Path

from PIL import Image

from app.services.mpc_export_service import MPCExportOptions, MPCExportService
from app.services.mpc_preflight_service import MPCPreflightService


def make_image(path: Path, size=(1600, 2400), color="red"):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def item(front="front.png", back="back.png", quantity=1, status="Default Back"):
    return {
        "filename": front,
        "relative_path": front,
        "effective_back_filename": back,
        "effective_back_relative_path": back,
        "back_status": status,
        "quantity": quantity,
    }


def test_preflight_passes_valid_project_and_full_standard_decks(tmp_path):
    processed = tmp_path / "processed"
    make_image(processed / "front.png")
    make_image(processed / "back.png", color="blue")

    result = MPCPreflightService(MPCExportService()).check(
        [item(quantity=4)],
        processed,
        MPCExportOptions(deck_capacity=2, split_custom_backs=True),
    )

    assert result["passed"]
    assert result["errors"] == 0
    assert result["warnings"] == 0
    assert result["plan"]["groups"][0]["unused_slots"] == 0


def test_preflight_blocks_missing_back_and_unreadable_front(tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    (processed / "front.png").write_text("not an image", encoding="utf-8")

    row = item()
    row["effective_back_relative_path"] = "missing.png"
    result = MPCPreflightService().check(
        [row],
        processed,
        MPCExportOptions(deck_capacity=2),
    )

    assert not result["passed"]
    assert result["errors"] >= 2
    categories = {issue.category for issue in result["issues"]}
    assert "Unreadable image" in categories
    assert "Back file" in categories


def test_preflight_warns_for_low_resolution_and_aspect_ratio(tmp_path):
    processed = tmp_path / "processed"
    make_image(processed / "front.png", size=(500, 500))
    make_image(processed / "back.png", size=(500, 500), color="blue")

    result = MPCPreflightService().check(
        [item()],
        processed,
        MPCExportOptions(deck_capacity=10, split_custom_backs=False),
    )

    assert result["passed"]
    assert result["warnings"] >= 4
    categories = {issue.category for issue in result["issues"]}
    assert "Resolution" in categories
    assert "Aspect ratio" in categories
