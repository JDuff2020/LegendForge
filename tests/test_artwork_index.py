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
