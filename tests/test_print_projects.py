from pathlib import Path
from PIL import Image
from app.services.database_service import DatabaseService
from app.services.artwork_index_service import ArtworkIndexService
from app.services.print_project_service import PrintProjectService

def test_print_project_crud_and_manifest(tmp_path):
    root=tmp_path/'processed'; (root/'Core').mkdir(parents=True); Image.new('RGB',(100,150)).save(root/'Core'/'Card.png')
    db=DatabaseService(tmp_path/'db.sqlite3'); ArtworkIndexService(db).index_folder(root,library_kind='processed'); s=PrintProjectService(db)
    pid=s.create_project('Test'); art=s.available_artwork()[0]; s.add_artwork(pid,[art['id']]); s.add_artwork(pid,[art['id']]); assert s.items(pid)[0]['quantity']==2
    item=s.items(pid)[0]; s.set_quantity(item['id'],3); assert s.items(pid)[0]['quantity']==3
    path=tmp_path/'manifest.csv'; s.export_csv(pid,path); assert 'Card.png' in path.read_text(encoding='utf-8-sig')
    copy=s.duplicate_project(pid,'Copy'); assert s.items(copy)[0]['quantity']==3



def test_csv_import_exact_path_filename_stem_and_combines_rows(tmp_path):
    root = tmp_path / "processed"
    (root / "Core").mkdir(parents=True)
    (root / "Other").mkdir(parents=True)
    Image.new("RGB", (100, 150)).save(root / "Core" / "Card.png")
    Image.new("RGB", (100, 150)).save(root / "Other" / "Unique.webp")

    db = DatabaseService(tmp_path / "db.sqlite3")
    ArtworkIndexService(db).index_folder(root, library_kind="processed")
    service = PrintProjectService(db)

    csv_path = tmp_path / "queue.csv"
    csv_path.write_text(
        "Order,Filename,Relative Path,Quantity\n"
        "2,Card.jpg,Core/Card.png,2\n"
        "1,Unique.png,,3\n"
        "3,Card.png,,4\n",
        encoding="utf-8",
    )

    analysis = service.analyze_csv(csv_path)
    assert analysis["matched"] == 3
    assert analysis["unmatched"] == 0
    project_id = service.create_project_from_csv(analysis, "Imported")
    items = service.items(project_id)

    assert [item["filename"] for item in items] == ["Unique.webp", "Card.png"]
    assert [item["quantity"] for item in items] == [3, 6]


def test_csv_import_flags_ambiguous_unmatched_and_invalid(tmp_path):
    root = tmp_path / "processed"
    (root / "A").mkdir(parents=True)
    (root / "B").mkdir(parents=True)
    Image.new("RGB", (100, 150)).save(root / "A" / "Same.png")
    Image.new("RGB", (100, 150)).save(root / "B" / "Same.jpg")

    db = DatabaseService(tmp_path / "db.sqlite3")
    ArtworkIndexService(db).index_folder(root, library_kind="processed")
    service = PrintProjectService(db)

    csv_path = tmp_path / "bad.csv"
    csv_path.write_text(
        "Filename,Quantity\n"
        "Same.webp,1\n"
        "Missing.png,2\n"
        "Anything.png,zero\n",
        encoding="utf-8",
    )

    analysis = service.analyze_csv(csv_path)
    assert analysis["ambiguous"] == 1
    assert analysis["unmatched"] == 1
    assert analysis["invalid"] == 1
    assert analysis["matched"] == 0


def test_default_and_custom_card_backs(tmp_path):
    root = tmp_path / "processed"
    root.mkdir()
    for name in ["Front.png", "DefaultBack.png", "CustomBack.png"]:
        Image.new("RGB", (100, 150)).save(root / name)
    db = DatabaseService(tmp_path / "db.sqlite3")
    ArtworkIndexService(db).index_folder(root, library_kind="processed")
    service = PrintProjectService(db)
    records = {r["filename"]: r for r in service.available_artwork()}
    project_id = service.create_project("Back Test")
    service.add_artwork(project_id, [records["Front.png"]["id"]])
    service.set_default_back(project_id, records["DefaultBack.png"]["id"])
    item = service.items(project_id)[0]
    assert item["back_status"] == "Default Back"
    assert item["effective_back_filename"] == "DefaultBack.png"
    service.set_item_back(item["id"], records["CustomBack.png"]["id"])
    item = service.items(project_id)[0]
    assert item["back_status"] == "Custom Back"
    assert item["effective_back_filename"] == "CustomBack.png"
    service.set_item_back(item["id"], None)
    assert service.items(project_id)[0]["effective_back_filename"] == "DefaultBack.png"


def test_csv_import_custom_back_and_export(tmp_path):
    root = tmp_path / "processed"
    root.mkdir()
    Image.new("RGB", (100, 150)).save(root / "Front.png")
    Image.new("RGB", (100, 150)).save(root / "Back.png")
    db = DatabaseService(tmp_path / "db.sqlite3")
    ArtworkIndexService(db).index_folder(root, library_kind="processed")
    service = PrintProjectService(db)
    csv_path = tmp_path / "import.csv"
    csv_path.write_text(
        "Filename,Quantity,Back Filename\nFront.png,2,Back.png\n",
        encoding="utf-8",
    )
    analysis = service.analyze_csv(csv_path)
    assert analysis["matched"] == 1
    assert analysis["custom_backs"] == 1
    project_id = service.create_project_from_csv(analysis, "Imported Backs")
    item = service.items(project_id)[0]
    assert item["back_status"] == "Custom Back"
    assert item["effective_back_filename"] == "Back.png"
    exported = tmp_path / "export.csv"
    service.export_csv(project_id, exported)
    content = exported.read_text(encoding="utf-8-sig")
    assert "Back Filename" in content
    assert "Back.png" in content


def test_schema_migrates_existing_print_tables(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as c:
        c.executescript("""
        CREATE TABLE app_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE artwork_processed(id INTEGER PRIMARY KEY, relative_path TEXT UNIQUE, filename TEXT, stem TEXT, extension TEXT, width INTEGER, height INTEGER, file_size INTEGER, modified_ns INTEGER, sha256 TEXT, readable INTEGER, indexed_at TEXT, original_id INTEGER, processor TEXT, processor_version TEXT, validation_status TEXT);
        CREATE TABLE print_projects(id INTEGER PRIMARY KEY, name TEXT UNIQUE, created_at TEXT, updated_at TEXT);
        CREATE TABLE print_project_items(id INTEGER PRIMARY KEY, project_id INTEGER, processed_artwork_id INTEGER, quantity INTEGER, sort_order INTEGER, UNIQUE(project_id,processed_artwork_id));
        """)
    DatabaseService(path)
    with sqlite3.connect(path) as c:
        project_cols = {row[1] for row in c.execute("PRAGMA table_info(print_projects)")}
        item_cols = {row[1] for row in c.execute("PRAGMA table_info(print_project_items)")}
    assert "default_back_artwork_id" in project_cols
    assert "back_artwork_id" in item_cols
