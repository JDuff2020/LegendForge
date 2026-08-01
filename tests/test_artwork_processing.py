from pathlib import Path

from PIL import Image

from app.models.processing import ProcessingPreset
from app.services.artwork_index_service import ArtworkIndexService
from app.services.artwork_processing_service import ArtworkProcessingService
from app.services.database_service import DatabaseService


def make_image(path: Path, size=(100, 200)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path)


def test_process_missing_preserves_folders_and_indexes_ready(tmp_path):
    originals = tmp_path / "originals"
    processed = tmp_path / "processed"
    make_image(originals / "Core" / "Hero.png")

    database = DatabaseService(tmp_path / "db.sqlite3")
    index = ArtworkIndexService(database)
    index.index_folder(originals, library_kind="original")
    records = index.pipeline_records("NEEDS_PROCESSING").records

    processor = ArtworkProcessingService(database)
    summary = processor.process_records(
        records,
        originals,
        processed,
        ProcessingPreset(width=300, height=400, output_format="JPEG", fit_mode="Contain"),
    )

    assert summary.completed == 1
    output = processed / "Core" / "Hero.jpg"
    assert output.is_file()
    with Image.open(output) as image:
        assert image.size == (300, 400)

    index.index_folder(processed, library_kind="processed")
    assert index.pipeline_counts()["READY"] == 1


def test_cover_and_original_size_modes(tmp_path):
    originals = tmp_path / "originals"
    make_image(originals / "Card.png", (100, 200))
    database = DatabaseService(tmp_path / "db.sqlite3")
    index = ArtworkIndexService(database)
    index.index_folder(originals)
    record = index.search().records[0]
    processor = ArtworkProcessingService(database)

    cover_root = tmp_path / "cover"
    processor.process_records(
        [record], originals, cover_root,
        ProcessingPreset(width=120, height=120, output_format="PNG", fit_mode="Cover"),
    )
    with Image.open(cover_root / "Card.png") as image:
        assert image.size == (120, 120)

    original_root = tmp_path / "original_size"
    processor.process_records(
        [record], originals, original_root,
        ProcessingPreset(width=1, height=1, output_format="PNG", fit_mode="Original size"),
    )
    with Image.open(original_root / "Card.png") as image:
        assert image.size == (100, 200)


def test_minimum_size_mode_scales_proportionally_without_forcing_exact_size(tmp_path):
    originals = tmp_path / "originals"
    make_image(originals / "Wide.png", (500, 500))
    database = DatabaseService(tmp_path / "db.sqlite3")
    index = ArtworkIndexService(database)
    index.index_folder(originals)
    record = index.search().records[0]
    processor = ArtworkProcessingService(database)

    output_root = tmp_path / "scaled"
    processor.process_records(
        [record],
        originals,
        output_root,
        ProcessingPreset(
            width=744,
            height=1039,
            output_format="PNG",
            fit_mode="Minimum size (proportional)",
        ),
    )

    with Image.open(output_root / "Wide.png") as image:
        assert image.size == (1039, 1039)
        assert image.width >= 744
        assert image.height >= 1039


def test_minimum_size_mode_does_not_downscale_compliant_artwork(tmp_path):
    originals = tmp_path / "originals"
    make_image(originals / "Large.png", (1500, 2100))
    database = DatabaseService(tmp_path / "db.sqlite3")
    index = ArtworkIndexService(database)
    index.index_folder(originals)
    record = index.search().records[0]
    processor = ArtworkProcessingService(database)

    output_root = tmp_path / "scaled"
    processor.process_records(
        [record],
        originals,
        output_root,
        ProcessingPreset(
            width=744,
            height=1039,
            output_format="PNG",
            fit_mode="Minimum size (proportional)",
        ),
    )

    with Image.open(output_root / "Large.png") as image:
        assert image.size == (1500, 2100)


def test_predict_output_and_scale_warnings(tmp_path):
    database = DatabaseService(tmp_path / "db.sqlite3")
    processor = ArtworkProcessingService(database)
    preset = ProcessingPreset(width=1600, height=2400, fit_mode="Minimum size (proportional)")
    size, scale = processor.predict_output(800, 1200, preset)
    assert size == (1600, 2400)
    assert scale == 2.0
    assert processor.scale_warning(scale) == "Moderate enlargement"


def test_processing_presets_round_trip(tmp_path):
    database = DatabaseService(tmp_path / "db.sqlite3")
    processor = ArtworkProcessingService(database)
    preset = ProcessingPreset(width=1600, height=2400, output_format="WEBP", quality=88)
    processor.save_preset("MPC", preset)
    assert processor.saved_preset_names() == ["MPC"]
    loaded = processor.load_preset("MPC")
    assert loaded == preset
    processor.delete_preset("MPC")
    assert processor.load_preset("MPC") is None



def test_mpc_bleed_mode_creates_minimum_canvas_and_32px_bleed(tmp_path):
    originals = tmp_path / "originals"
    processed = tmp_path / "processed"
    # This size is the MPC trim area at the minimum output resolution.
    make_image(originals / "Card.png", (752, 1046))

    database = DatabaseService(tmp_path / "db.sqlite3")
    index = ArtworkIndexService(database)
    index.index_folder(originals)
    record = index.search().records[0]
    processor = ArtworkProcessingService(database)
    preset = ProcessingPreset(
        width=816,
        height=1110,
        output_format="PNG",
        fit_mode="MPC bleed (edge extend)",
        bleed_px_at_minimum=32,
    )

    assert processor.scaled_bleed(preset) == (32, 32)
    assert processor.trim_dimensions(preset) == (752, 1046)

    summary = processor.process_records(
        [record],
        originals,
        processed,
        preset,
    )
    assert summary.completed == 1
    with Image.open(processed / "Card.png") as image:
        assert image.size == (816, 1110)
        # Solid source artwork should extend continuously into every corner.
        assert image.getpixel((0, 0)) == image.getpixel((408, 555))


def test_mpc_bleed_scales_with_larger_output_resolution(tmp_path):
    database = DatabaseService(tmp_path / "db.sqlite3")
    processor = ArtworkProcessingService(database)
    preset = ProcessingPreset(
        width=1632,
        height=2220,
        fit_mode="MPC bleed (edge extend)",
        bleed_px_at_minimum=32,
    )

    assert processor.scaled_bleed(preset) == (64, 64)
    assert processor.trim_dimensions(preset) == (1504, 2092)

    predicted, scale = processor.predict_output(752, 1046, preset)
    assert predicted == (1632, 2220)
    assert scale == 2.0


def test_processing_preset_round_trip_preserves_bleed_setting(tmp_path):
    database = DatabaseService(tmp_path / "db.sqlite3")
    processor = ArtworkProcessingService(database)
    preset = ProcessingPreset(
        width=816,
        height=1110,
        fit_mode="MPC bleed (edge extend)",
        bleed_px_at_minimum=32,
    )
    processor.save_preset("MPC Bleed", preset)
    assert processor.load_preset("MPC Bleed") == preset
