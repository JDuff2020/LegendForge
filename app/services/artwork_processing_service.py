from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageColor, ImageOps

from app.models.artwork_record import ArtworkRecord
from app.models.processing import ProcessingItemResult, ProcessingPreset, ProcessingSummary
from app.services.database_service import DatabaseService

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


class ArtworkProcessingService:
    """Deterministic local artwork processor backed by Pillow."""

    PROCESSOR_NAME = "LegendForge Pillow"
    PROCESSOR_VERSION = "2"

    def __init__(self, database: DatabaseService):
        self.database = database

    def process_records(
        self,
        records: list[ArtworkRecord],
        original_root: Path,
        processed_root: Path,
        preset: ProcessingPreset,
        progress_callback: ProgressCallback | None = None,
        cancel_callback: CancelCallback | None = None,
    ) -> ProcessingSummary:
        original_root = Path(original_root).resolve()
        processed_root = Path(processed_root).resolve()
        if not original_root.is_dir():
            raise FileNotFoundError(f"Original artwork folder does not exist: {original_root}")
        processed_root.mkdir(parents=True, exist_ok=True)

        summary = ProcessingSummary(total=len(records))
        for position, record in enumerate(records, 1):
            if cancel_callback and cancel_callback():
                summary.cancelled = len(records) - position + 1
                break

            if progress_callback:
                progress_callback(position, len(records), record.relative_path)

            source_path = record.absolute_path(original_root)
            relative = Path(record.relative_path)
            output_relative = relative.with_suffix(preset.extension)
            output_path = processed_root / output_relative
            job_id = self._create_job(int(record.id), "Pillow")

            try:
                self._mark_job_started(job_id)
                self._process_one(source_path, output_path, preset)
                self._mark_job_finished(job_id, "COMPLETE", str(output_relative))
                summary.completed += 1
                summary.results.append(
                    ProcessingItemResult(int(record.id), source_path, output_path, "COMPLETE")
                )
            except Exception as exc:
                self._mark_job_finished(job_id, "FAILED", str(exc))
                summary.failed += 1
                summary.results.append(
                    ProcessingItemResult(int(record.id), source_path, None, "FAILED", str(exc))
                )

        return summary


    @staticmethod
    def predict_output(width: int, height: int, preset: ProcessingPreset) -> tuple[tuple[int, int], float]:
        """Return predicted output dimensions and uniform scale factor."""
        width = max(1, int(width))
        height = max(1, int(height))
        target = (max(1, preset.width), max(1, preset.height))
        mode = preset.fit_mode.casefold()
        if mode == "original size":
            return (width, height), 1.0
        if mode == "minimum size (proportional)":
            scale = max(target[0] / width, target[1] / height, 1.0)
            return (max(target[0], round(width * scale)), max(target[1], round(height * scale))), scale
        if mode == "contain":
            scale = min(target[0] / width, target[1] / height)
            return target, scale
        if mode == "cover":
            scale = max(target[0] / width, target[1] / height)
            return target, scale
        if mode == "stretch":
            return target, max(target[0] / width, target[1] / height)
        return target, 1.0

    @staticmethod
    def scale_warning(scale: float) -> str:
        if scale >= 4.0:
            return "Severe enlargement: likely blurry"
        if scale >= 2.5:
            return "Heavy enlargement may appear blurry"
        if scale >= 1.75:
            return "Moderate enlargement"
        return ""

    def saved_preset_names(self) -> list[str]:
        presets = self._preset_store()
        return sorted(presets, key=str.casefold)

    def save_preset(self, name: str, preset: ProcessingPreset) -> None:
        presets = self._preset_store()
        presets[name] = {
            "width": preset.width, "height": preset.height,
            "output_format": preset.output_format, "quality": preset.quality,
            "fit_mode": preset.fit_mode, "background": preset.background,
        }
        self._write_preset_store(presets)

    def load_preset(self, name: str) -> ProcessingPreset | None:
        value = self._preset_store().get(name)
        if not isinstance(value, dict):
            return None
        try:
            return ProcessingPreset(**value)
        except TypeError:
            return None

    def delete_preset(self, name: str) -> None:
        presets = self._preset_store()
        presets.pop(name, None)
        self._write_preset_store(presets)

    def _preset_store(self) -> dict[str, dict[str, object]]:
        raw = self.database.scalar("SELECT value FROM app_metadata WHERE key='processing_presets'")
        if not raw:
            return {}
        try:
            value = json.loads(str(raw))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _write_preset_store(self, presets: dict[str, dict[str, object]]) -> None:
        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO app_metadata(key,value) VALUES('processing_presets',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(presets, sort_keys=True),),
            )

    def _process_one(self, source_path: Path, output_path: Path, preset: ProcessingPreset) -> None:
        if not source_path.is_file():
            raise FileNotFoundError(source_path)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = output_path.with_name(output_path.name + ".tmp")
        try:
            with Image.open(source_path) as source:
                source.load()
                image = self._transform(source, preset)
                save_kwargs: dict[str, object] = {}
                output_format = preset.output_format.upper()

                if output_format == "JPEG":
                    if image.mode not in {"RGB", "L"}:
                        background = Image.new("RGB", image.size, ImageColor.getrgb(preset.background))
                        if "A" in image.getbands():
                            background.paste(image, mask=image.getchannel("A"))
                        else:
                            background.paste(image)
                        image = background
                    save_kwargs.update(quality=preset.quality, optimize=True)
                elif output_format == "WEBP":
                    save_kwargs.update(quality=preset.quality, method=6)
                elif output_format == "PNG":
                    save_kwargs.update(optimize=True)

                image.save(temp_path, format=output_format, **save_kwargs)
                with Image.open(temp_path) as validation:
                    validation.verify()
                os.replace(temp_path, output_path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _transform(source: Image.Image, preset: ProcessingPreset) -> Image.Image:
        mode = preset.fit_mode.casefold()
        target = (max(1, preset.width), max(1, preset.height))
        image = ImageOps.exif_transpose(source)

        if mode == "original size":
            return image.copy()
        if mode == "minimum size (proportional)":
            # Treat width and height as minimum requirements. Scale both
            # dimensions by the same percentage until both minimums are met.
            # Never shrink artwork that already satisfies the requirements.
            scale = max(
                target[0] / max(1, image.width),
                target[1] / max(1, image.height),
                1.0,
            )
            if scale == 1.0:
                return image.copy()
            scaled_size = (
                max(target[0], round(image.width * scale)),
                max(target[1], round(image.height * scale)),
            )
            return image.resize(scaled_size, Image.Resampling.LANCZOS)
        if mode == "stretch":
            return image.resize(target, Image.Resampling.LANCZOS)
        if mode == "cover":
            return ImageOps.fit(image, target, method=Image.Resampling.LANCZOS)

        # Contain: preserve the full image and pad to target dimensions.
        contained = ImageOps.contain(image, target, method=Image.Resampling.LANCZOS)
        use_alpha = "A" in contained.getbands() and preset.output_format.upper() != "JPEG"
        canvas_mode = "RGBA" if use_alpha else "RGB"
        background = (0, 0, 0, 0) if use_alpha else ImageColor.getrgb(preset.background)
        canvas = Image.new(canvas_mode, target, background)
        x = (target[0] - contained.width) // 2
        y = (target[1] - contained.height) // 2
        if use_alpha:
            canvas.paste(contained, (x, y), contained)
        else:
            if contained.mode != "RGB":
                contained = contained.convert("RGB")
            canvas.paste(contained, (x, y))
        return canvas

    def _create_job(self, original_id: int, backend: str) -> int:
        with self.database.connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO processing_jobs(original_id, backend, status, created_at)
                VALUES(?, ?, 'PENDING', ?)
                """,
                (original_id, backend, self._now()),
            )
            return int(cursor.lastrowid)

    def _mark_job_started(self, job_id: int) -> None:
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE processing_jobs SET status='RUNNING', started_at=? WHERE id=?",
                (self._now(), job_id),
            )

    def _mark_job_finished(self, job_id: int, status: str, message: str) -> None:
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE processing_jobs
                SET status=?, finished_at=?, message=?
                WHERE id=?
                """,
                (status, self._now(), message, job_id),
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
