from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from app.models.artwork_record import ArtworkRecord
from app.models.index_results import (
    ArtworkIndexProgress,
    ArtworkIndexSummary,
    ArtworkSearchResult,
)
from app.services.database_service import DatabaseService

SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
ProgressCallback = Callable[[ArtworkIndexProgress], None]
_HASH_CHUNK_SIZE = 1024 * 1024


class ArtworkIndexService:
    def __init__(self, database: DatabaseService):
        self.database = database

    def index_folder(
        self,
        root: Path,
        progress_callback: ProgressCallback | None = None,
        library_kind: str = "original",
    ) -> ArtworkIndexSummary:
        root = Path(root).resolve()
        table = self._table(library_kind)
        if not root.is_dir():
            raise FileNotFoundError(f"Artwork folder does not exist: {root}")

        started = time.perf_counter()
        files = sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        summary = ArtworkIndexSummary(
            discovered=len(files),
            database_path=str(self.database.database_path),
        )

        with self.database.connection() as connection:
            existing = {
                row["relative_path"]: (row["file_size"], row["modified_ns"])
                for row in connection.execute(
                    f"SELECT relative_path, file_size, modified_ns FROM {table}"
                )
            }
            seen: set[str] = set()

            for position, path in enumerate(files, 1):
                relative_path = path.relative_to(root).as_posix()
                seen.add(relative_path)
                stat = path.stat()
                prior = existing.get(relative_path)

                if prior == (stat.st_size, stat.st_mtime_ns):
                    summary.unchanged += 1
                else:
                    record = self._read(
                        path,
                        relative_path,
                        stat.st_size,
                        stat.st_mtime_ns,
                        library_kind,
                    )
                    summary.added += prior is None
                    summary.updated += prior is not None
                    summary.unreadable += not record.readable
                    self._upsert(connection, table, record)

                if progress_callback:
                    progress_callback(
                        ArtworkIndexProgress(position, len(files), relative_path)
                    )

            removed = sorted(set(existing) - seen)
            if removed:
                connection.executemany(
                    f"DELETE FROM {table} WHERE relative_path = ?",
                    ((path,) for path in removed),
                )
                summary.removed = len(removed)

            connection.execute(
                """
                INSERT INTO app_metadata(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (f"{library_kind}_root", str(root)),
            )

        summary.elapsed_seconds = round(time.perf_counter() - started, 3)
        return summary

    def search(
        self,
        query: str = "",
        extension: str | None = None,
        readable_only: bool = False,
        limit: int = 500,
        offset: int = 0,
        library_kind: str = "original",
    ) -> ArtworkSearchResult:
        table = self._table(library_kind)
        clauses: list[str] = []
        parameters: list[object] = []

        if query.strip():
            clauses.append(
                "(filename LIKE ? COLLATE NOCASE OR relative_path LIKE ? COLLATE NOCASE)"
            )
            search_term = f"%{query.strip()}%"
            parameters += [search_term, search_term]

        if extension and extension != "All":
            normalized_extension = extension.lower()
            if not normalized_extension.startswith("."):
                normalized_extension = "." + normalized_extension
            clauses.append("extension = ?")
            parameters.append(normalized_extension)

        if readable_only:
            clauses.append("readable = 1")

        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.database.connection() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM {table} {where}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT * FROM {table} {where}
                ORDER BY filename COLLATE NOCASE
                LIMIT ? OFFSET ?
                """,
                (*parameters, limit, offset),
            ).fetchall()

        records = [
            ArtworkRecord(
                row["id"],
                library_kind,
                row["relative_path"],
                row["filename"],
                row["stem"],
                row["extension"],
                row["width"],
                row["height"],
                row["file_size"],
                row["modified_ns"],
                row["sha256"],
                bool(row["readable"]),
                row["indexed_at"],
            )
            for row in rows
        ]
        return ArtworkSearchResult(records, total)

    def extensions(self, library_kind: str = "original") -> list[str]:
        table = self._table(library_kind)
        with self.database.connection() as connection:
            return [
                row[0]
                for row in connection.execute(
                    f"SELECT DISTINCT extension FROM {table} ORDER BY extension"
                )
            ]

    def statistics(self, library_kind: str = "original") -> dict[str, int]:
        """Return database-backed health statistics for one artwork library.

        ``duplicate_groups`` counts hashes shared by two or more records.
        ``duplicate_files`` counts extra copies beyond the first file in each group.
        """
        table = self._table(library_kind)
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(CASE WHEN readable = 1 THEN 1 ELSE 0 END), 0) AS readable,
                    COALESCE(SUM(CASE WHEN readable = 0 THEN 1 ELSE 0 END), 0) AS unreadable
                FROM {table}
                """
            ).fetchone()
            duplicate_row = connection.execute(
                f"""
                SELECT
                    COUNT(*) AS duplicate_groups,
                    COALESCE(SUM(file_count - 1), 0) AS duplicate_files
                FROM (
                    SELECT COUNT(*) AS file_count
                    FROM {table}
                    WHERE sha256 <> ''
                    GROUP BY sha256
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()

        return {
            "total": int(row["total"]),
            "readable": int(row["readable"]),
            "unreadable": int(row["unreadable"]),
            "duplicate_groups": int(duplicate_row["duplicate_groups"]),
            "duplicate_files": int(duplicate_row["duplicate_files"]),
        }

    def duplicate_groups(
        self, library_kind: str = "original", limit: int = 100
    ) -> list[dict[str, object]]:
        """Return duplicate hash groups without accessing the filesystem."""
        table = self._table(library_kind)
        with self.database.connection() as connection:
            groups = connection.execute(
                f"""
                SELECT sha256, COUNT(*) AS file_count
                FROM {table}
                WHERE sha256 <> ''
                GROUP BY sha256
                HAVING COUNT(*) > 1
                ORDER BY file_count DESC, sha256
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            results: list[dict[str, object]] = []
            for group in groups:
                paths = [
                    row[0]
                    for row in connection.execute(
                        f"""
                        SELECT relative_path
                        FROM {table}
                        WHERE sha256 = ?
                        ORDER BY relative_path COLLATE NOCASE
                        """,
                        (group["sha256"],),
                    )
                ]
                results.append(
                    {
                        "sha256": group["sha256"],
                        "file_count": int(group["file_count"]),
                        "relative_paths": paths,
                    }
                )

        return results

    @staticmethod
    def _match_key(relative_path: str) -> str:
        """Match originals and outputs by relative folder plus filename stem."""
        path = Path(relative_path)
        return (path.parent / path.stem).as_posix().casefold()

    def pipeline_snapshot(self) -> dict[str, object]:
        """Return pipeline counts and record IDs for each actionable state."""
        with self.database.connection() as connection:
            originals = connection.execute(
                "SELECT * FROM artwork_original ORDER BY relative_path COLLATE NOCASE"
            ).fetchall()
            processed = connection.execute(
                "SELECT * FROM artwork_processed ORDER BY relative_path COLLATE NOCASE"
            ).fetchall()

        processed_by_key: dict[str, list] = {}
        for row in processed:
            processed_by_key.setdefault(self._match_key(row["relative_path"]), []).append(row)

        statuses: dict[str, list[int]] = {
            "READY": [],
            "NEEDS_PROCESSING": [],
            "NEEDS_REBUILD": [],
            "UNREADABLE_ORIGINAL": [],
            "UNREADABLE_PROCESSED": [],
        }
        matched_processed_ids: set[int] = set()

        for original in originals:
            original_id = int(original["id"])
            if not bool(original["readable"]):
                statuses["UNREADABLE_ORIGINAL"].append(original_id)
                continue

            matches = processed_by_key.get(self._match_key(original["relative_path"]), [])
            if not matches:
                statuses["NEEDS_PROCESSING"].append(original_id)
                continue

            matched_processed_ids.update(int(row["id"]) for row in matches)
            readable_matches = [row for row in matches if bool(row["readable"])]
            if not readable_matches:
                statuses["UNREADABLE_PROCESSED"].append(original_id)
            elif max(int(row["modified_ns"]) for row in readable_matches) < int(original["modified_ns"]):
                statuses["NEEDS_REBUILD"].append(original_id)
            else:
                statuses["READY"].append(original_id)

        orphaned_processed = [
            int(row["id"]) for row in processed if int(row["id"]) not in matched_processed_ids
        ]
        counts = {name: len(ids) for name, ids in statuses.items()}
        counts["TOTAL"] = len(originals)
        counts["PROCESSED_TOTAL"] = len(processed)
        counts["ORPHANED_PROCESSED"] = len(orphaned_processed)
        return {
            "counts": counts,
            "original_ids": statuses,
            "processed_ids": {"ORPHANED_PROCESSED": orphaned_processed},
        }

    def pipeline_counts(self) -> dict[str, int]:
        return dict(self.pipeline_snapshot()["counts"])

    def pipeline_records(self, status: str) -> ArtworkSearchResult:
        """Return all records represented by a pipeline dashboard status."""
        snapshot = self.pipeline_snapshot()
        if status == "ORPHANED_PROCESSED":
            ids = snapshot["processed_ids"].get(status, [])
            library_kind = "processed"
        else:
            ids = snapshot["original_ids"].get(status, [])
            library_kind = "original"

        if not ids:
            return ArtworkSearchResult([], 0)

        table = self._table(library_kind)
        placeholders = ",".join("?" for _ in ids)
        with self.database.connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE id IN ({placeholders}) ORDER BY filename COLLATE NOCASE",
                tuple(ids),
            ).fetchall()
        records = [
            ArtworkRecord(
                row["id"], library_kind, row["relative_path"], row["filename"],
                row["stem"], row["extension"], row["width"], row["height"],
                row["file_size"], row["modified_ns"], row["sha256"],
                bool(row["readable"]), row["indexed_at"], status,
            )
            for row in rows
        ]
        return ArtworkSearchResult(records, len(records))

    def _table(self, kind: str) -> str:
        if kind not in {"original", "processed"}:
            raise ValueError(kind)
        return "artwork_original" if kind == "original" else "artwork_processed"

    def _read(
        self,
        path: Path,
        relative_path: str,
        size: int,
        modified_ns: int,
        library_kind: str,
    ) -> ArtworkRecord:
        readable = True
        width = height = 0
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
        except Exception:
            readable = False

        sha256 = self._sha256(path)
        return ArtworkRecord(
            None,
            library_kind,
            relative_path,
            path.name,
            path.stem,
            path.suffix.lower(),
            width,
            height,
            size,
            modified_ns,
            sha256,
            readable,
            self._now(),
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as file:
            while chunk := file.read(_HASH_CHUNK_SIZE):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _upsert(self, connection, table: str, record: ArtworkRecord) -> None:
        connection.execute(
            f"""
            INSERT INTO {table}(
                relative_path, filename, stem, extension, width, height,
                file_size, modified_ns, sha256, readable, indexed_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(relative_path) DO UPDATE SET
                filename = excluded.filename,
                stem = excluded.stem,
                extension = excluded.extension,
                width = excluded.width,
                height = excluded.height,
                file_size = excluded.file_size,
                modified_ns = excluded.modified_ns,
                sha256 = excluded.sha256,
                readable = excluded.readable,
                indexed_at = excluded.indexed_at
            """,
            (
                record.relative_path,
                record.filename,
                record.stem,
                record.extension,
                record.width,
                record.height,
                record.file_size,
                record.modified_ns,
                record.sha256,
                int(record.readable),
                record.indexed_at,
            ),
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
