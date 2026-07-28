from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 2

class DatabaseService:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback(); raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS app_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artwork_original(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              relative_path TEXT NOT NULL UNIQUE, filename TEXT NOT NULL, stem TEXT NOT NULL,
              extension TEXT NOT NULL, width INTEGER NOT NULL DEFAULT 0, height INTEGER NOT NULL DEFAULT 0,
              file_size INTEGER NOT NULL, modified_ns INTEGER NOT NULL, sha256 TEXT NOT NULL,
              readable INTEGER NOT NULL, indexed_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artwork_processed(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              relative_path TEXT NOT NULL UNIQUE, filename TEXT NOT NULL, stem TEXT NOT NULL,
              extension TEXT NOT NULL, width INTEGER NOT NULL DEFAULT 0, height INTEGER NOT NULL DEFAULT 0,
              file_size INTEGER NOT NULL, modified_ns INTEGER NOT NULL, sha256 TEXT NOT NULL,
              readable INTEGER NOT NULL, indexed_at TEXT NOT NULL,
              original_id INTEGER, processor TEXT, processor_version TEXT, validation_status TEXT,
              FOREIGN KEY(original_id) REFERENCES artwork_original(id) ON DELETE SET NULL);
            CREATE TABLE IF NOT EXISTS processing_jobs(
              id INTEGER PRIMARY KEY AUTOINCREMENT, original_id INTEGER NOT NULL,
              backend TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
              started_at TEXT, finished_at TEXT, message TEXT,
              FOREIGN KEY(original_id) REFERENCES artwork_original(id) ON DELETE CASCADE);
            CREATE INDEX IF NOT EXISTS idx_original_stem ON artwork_original(stem COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_processed_stem ON artwork_processed(stem COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_original_sha ON artwork_original(sha256);
            CREATE INDEX IF NOT EXISTS idx_processed_sha ON artwork_processed(sha256);
            """)
            c.execute("INSERT INTO app_metadata(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(SCHEMA_VERSION),))

    def scalar(self, sql: str, parameters: tuple = ()) -> object | None:
        with self.connection() as c:
            row = c.execute(sql, parameters).fetchone()
            return None if row is None else row[0]
