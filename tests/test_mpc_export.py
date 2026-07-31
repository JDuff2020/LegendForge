from pathlib import Path

from PIL import Image

from app.services.mpc_export_service import MPCExportOptions, MPCExportService


def make_image(path: Path, color: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 150), color).save(path)


def test_split_export_groups_standard_and_custom_backs(tmp_path):
    processed = tmp_path / "processed"
    make_image(processed / "front_a.png", "red")
    make_image(processed / "front_b.png", "green")
    make_image(processed / "default.png", "blue")
    make_image(processed / "custom.png", "yellow")

    items = [
        {
            "filename": "front_a.png",
            "relative_path": "front_a.png",
            "effective_back_filename": "default.png",
            "effective_back_relative_path": "default.png",
            "back_status": "Default Back",
            "quantity": 3,
        },
        {
            "filename": "front_b.png",
            "relative_path": "front_b.png",
            "effective_back_filename": "custom.png",
            "effective_back_relative_path": "custom.png",
            "back_status": "Custom Back",
            "quantity": 2,
        },
    ]

    result = MPCExportService().export(
        items,
        processed,
        tmp_path / "export",
        MPCExportOptions(deck_capacity=2, split_custom_backs=True),
        "Project",
    )

    assert result["total_cards"] == 5
    assert result["standard_cards"] == 2
    assert result["custom_cards"] == 3
    assert result["true_custom_cards"] == 2
    assert result["transferred_standard_cards"] == 1
    assert result["total_decks"] == 3
    assert (tmp_path / "export/Standard_Back/Deck_001_2_cards/Fronts").is_dir()
    assert (tmp_path / "export/Standard_Back/Deck_001_2_cards/Back").is_dir()
    assert (tmp_path / "export/Custom_Backs/Deck_001_2_cards/Backs").is_dir()


def test_custom_front_and_back_names_have_matching_prefixes(tmp_path):
    processed = tmp_path / "processed"
    make_image(processed / "front.png", "red")
    make_image(processed / "back.png", "blue")
    items = [{
        "filename": "front.png",
        "relative_path": "front.png",
        "effective_back_filename": "back.png",
        "effective_back_relative_path": "back.png",
        "back_status": "Custom Back",
        "quantity": 1,
    }]
    MPCExportService().export(
        items,
        processed,
        tmp_path / "export",
        MPCExportOptions(deck_capacity=18),
        "Project",
    )
    deck = tmp_path / "export/Custom_Backs/Deck_001_1_cards"
    front = next((deck / "Fronts").iterdir())
    back = next((deck / "Backs").iterdir())
    assert front.name.startswith("0001_")
    assert back.name.startswith("0001_")



def test_standard_back_group_has_no_unused_slots_and_remainder_moves_to_custom(tmp_path):
    processed = tmp_path / "processed"
    make_image(processed / "standard_front.png", "red")
    make_image(processed / "custom_front.png", "green")
    make_image(processed / "default.png", "blue")
    make_image(processed / "custom.png", "yellow")

    items = [
        {
            "filename": "standard_front.png",
            "relative_path": "standard_front.png",
            "effective_back_filename": "default.png",
            "effective_back_relative_path": "default.png",
            "back_status": "Default Back",
            "quantity": 7,
        },
        {
            "filename": "custom_front.png",
            "relative_path": "custom_front.png",
            "effective_back_filename": "custom.png",
            "effective_back_relative_path": "custom.png",
            "back_status": "Custom Back",
            "quantity": 1,
        },
    ]

    service = MPCExportService()
    plan = service.plan(
        items,
        processed,
        MPCExportOptions(deck_capacity=3, split_custom_backs=True),
    )

    standard_group = next(
        group for group in plan["groups"] if group["name"] == "Standard_Back"
    )
    custom_group = next(
        group for group in plan["groups"] if group["name"] == "Custom_Backs"
    )

    assert standard_group["deck_sizes"] == [3, 3]
    assert standard_group["unused_slots"] == 0
    assert plan["transferred_standard_cards"] == 1
    assert plan["true_custom_cards"] == 1
    assert custom_group["card_count"] == 2

    result = service.export(
        items,
        processed,
        tmp_path / "export",
        MPCExportOptions(deck_capacity=3, split_custom_backs=True),
        "Project",
    )

    custom_deck = tmp_path / "export/Custom_Backs/Deck_001_2_cards"
    back_files = sorted((custom_deck / "Backs").iterdir())
    assert len(back_files) == 2
    assert any("default" in path.name.lower() for path in back_files)
    assert any("custom" in path.name.lower() for path in back_files)
    assert result["groups"][0]["unused_slots"] == 0
