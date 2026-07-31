from pathlib import Path

from PIL import Image

from app.services.artwork_index_service import ArtworkIndexService
from app.services.database_service import DatabaseService


def make_image(path: Path, size=(100, 140)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path)


def test_incremental_index_search_and_delete(tmp_path):
    artwork = tmp_path / "artwork"
    make_image(artwork / "X-Men" / "Wolverine.jpg")
    make_image(artwork / "Avengers" / "Iron Man.png", (120, 170))

    service = ArtworkIndexService(DatabaseService(tmp_path / "legendforge.sqlite3"))
    first = service.index_folder(artwork)
    assert first.added == 2
    assert first.unchanged == 0

    second = service.index_folder(artwork)
    assert second.added == 0
    assert second.unchanged == 2

    result = service.search("wolverine")
    assert result.total_matches == 1
    assert result.records[0].width == 100
    assert result.records[0].height == 140

    (artwork / "X-Men" / "Wolverine.jpg").unlink()
    third = service.index_folder(artwork)
    assert third.removed == 1
    assert service.search().total_matches == 1


def test_changed_file_is_updated(tmp_path):
    artwork = tmp_path / "artwork"
    image = artwork / "Card.png"
    make_image(image, (100, 140))
    service = ArtworkIndexService(DatabaseService(tmp_path / "db.sqlite3"))
    service.index_folder(artwork)

    make_image(image, (200, 280))
    summary = service.index_folder(artwork)
    result = service.search("Card")
    assert summary.updated == 1
    assert result.records[0].width == 200


def test_statistics_and_duplicate_groups(tmp_path):
    artwork = tmp_path / "artwork"
    make_image(artwork / "First.png")
    make_image(artwork / "Second.png")
    make_image(artwork / "Different.png", (200, 280))

    service = ArtworkIndexService(DatabaseService(tmp_path / "db.sqlite3"))
    service.index_folder(artwork)

    statistics = service.statistics()
    assert statistics == {
        "total": 3,
        "readable": 3,
        "unreadable": 0,
        "duplicate_groups": 1,
        "duplicate_files": 1,
    }

    groups = service.duplicate_groups()
    assert len(groups) == 1
    assert groups[0]["file_count"] == 2
    assert groups[0]["relative_paths"] == ["First.png", "Second.png"]


def test_pipeline_statuses_match_relative_path_and_stem(tmp_path):
    originals = tmp_path / "originals"
    processed = tmp_path / "processed"
    make_image(originals / "Core" / "Ready.png")
    make_image(originals / "Core" / "Missing.png")
    make_image(originals / "Other" / "SameName.png")
    make_image(processed / "Core" / "Ready.jpg")
    make_image(processed / "Core" / "Orphan.png")

    service = ArtworkIndexService(DatabaseService(tmp_path / "db.sqlite3"))
    service.index_folder(originals, library_kind="original")
    service.index_folder(processed, library_kind="processed")

    counts = service.pipeline_counts()
    assert counts["TOTAL"] == 3
    assert counts["READY"] == 1
    assert counts["NEEDS_PROCESSING"] == 2
    assert counts["ORPHANED_PROCESSED"] == 1

    missing = service.pipeline_records("NEEDS_PROCESSING")
    assert {record.filename for record in missing.records} == {"Missing.png", "SameName.png"}
    orphaned = service.pipeline_records("ORPHANED_PROCESSED")
    assert [record.filename for record in orphaned.records] == ["Orphan.png"]
