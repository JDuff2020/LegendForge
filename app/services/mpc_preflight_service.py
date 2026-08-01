from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.services.mpc_export_service import MPCExportOptions, MPCExportService


@dataclass(frozen=True)
class PreflightIssue:
    severity: str
    category: str
    filename: str
    message: str


class MPCPreflightService:
    """Validate a print project before creating an MPC upload package."""

    INVALID_FILENAME_CHARACTERS = '<>:"/\\|?*'

    def __init__(self, export_service: MPCExportService | None = None):
        self.export_service = export_service or MPCExportService()

    @staticmethod
    def _inspect_image(path: Path) -> tuple[int, int]:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            return image.size

    @staticmethod
    def _issue(
        issues: list[PreflightIssue],
        severity: str,
        category: str,
        filename: str,
        message: str,
    ) -> None:
        issues.append(
            PreflightIssue(
                severity=severity,
                category=category,
                filename=filename,
                message=message,
            )
        )

    def check(
        self,
        items: list[dict],
        processed_root: Path,
        options: MPCExportOptions,
        minimum_width: int = 1600,
        minimum_height: int = 2400,
        maximum_file_size_mb: int = 30,
    ) -> dict:
        processed_root = Path(processed_root)
        issues: list[PreflightIssue] = []
        checked_paths: dict[Path, tuple[int, int] | None] = {}
        total_copies = 0

        if not processed_root.is_dir():
            self._issue(
                issues,
                "Error",
                "Project",
                "",
                f"Processed artwork folder does not exist: {processed_root}",
            )
            return self._result(issues, 0, None, 0)

        if not items:
            self._issue(
                issues,
                "Error",
                "Project",
                "",
                "The print project contains no cards.",
            )
            return self._result(issues, 0, None, 0)

        def inspect(path: Path, display_name: str, side: str) -> tuple[int, int] | None:
            resolved = path.resolve()
            if resolved in checked_paths:
                return checked_paths[resolved]

            if not path.is_file():
                self._issue(
                    issues,
                    "Error",
                    f"{side} file",
                    display_name,
                    f"File is missing: {path}",
                )
                checked_paths[resolved] = None
                return None

            invalid = [char for char in path.name if char in self.INVALID_FILENAME_CHARACTERS]
            if invalid:
                self._issue(
                    issues,
                    "Warning",
                    "Filename",
                    display_name,
                    "Filename contains characters that will be replaced during export: "
                    + " ".join(sorted(set(invalid))),
                )

            size_mb = path.stat().st_size / (1024 * 1024)
            if size_mb > maximum_file_size_mb:
                self._issue(
                    issues,
                    "Warning",
                    "File size",
                    display_name,
                    f"{side} file is {size_mb:.1f} MB, above the "
                    f"{maximum_file_size_mb} MB review threshold.",
                )

            try:
                dimensions = self._inspect_image(path)
            except Exception as exc:
                self._issue(
                    issues,
                    "Error",
                    "Unreadable image",
                    display_name,
                    f"{side} image cannot be read: {exc}",
                )
                checked_paths[resolved] = None
                return None

            width, height = dimensions
            if width < minimum_width or height < minimum_height:
                self._issue(
                    issues,
                    "Warning",
                    "Resolution",
                    display_name,
                    f"{side} image is {width} × {height}; recommended minimum is "
                    f"{minimum_width} × {minimum_height}.",
                )

            target_ratio = minimum_width / minimum_height
            actual_ratio = width / height if height else 0
            if actual_ratio and abs(actual_ratio - target_ratio) / target_ratio > 0.03:
                self._issue(
                    issues,
                    "Warning",
                    "Aspect ratio",
                    display_name,
                    f"{side} image aspect ratio differs from the configured card "
                    f"ratio by more than 3% ({width} × {height}).",
                )

            checked_paths[resolved] = dimensions
            return dimensions

        for item in items:
            quantity = int(item.get("quantity", 0))
            if quantity < 1:
                self._issue(
                    issues,
                    "Error",
                    "Quantity",
                    str(item.get("filename", "")),
                    "Quantity must be at least 1.",
                )
                continue
            total_copies += quantity

            front_relative = item.get("relative_path")
            filename = str(item.get("filename") or front_relative or "")
            if not front_relative:
                self._issue(
                    issues,
                    "Error",
                    "Front file",
                    filename,
                    "No front relative path is assigned.",
                )
            else:
                inspect(processed_root / front_relative, filename, "Front")

            back_relative = item.get("effective_back_relative_path")
            back_filename = str(
                item.get("effective_back_filename") or back_relative or filename
            )
            if not back_relative:
                self._issue(
                    issues,
                    "Error",
                    "Card back",
                    filename,
                    "No effective default or custom back is assigned.",
                )
            else:
                inspect(processed_root / back_relative, back_filename, "Back")

        plan = None
        if not any(issue.severity == "Error" for issue in issues):
            try:
                plan = self.export_service.plan(items, processed_root, options)
            except Exception as exc:
                self._issue(
                    issues,
                    "Error",
                    "Deck plan",
                    "",
                    str(exc),
                )

        if plan:
            standard_group = next(
                (
                    group
                    for group in plan["groups"]
                    if group["name"] == "Standard_Back"
                ),
                None,
            )
            if standard_group and standard_group["unused_slots"] != 0:
                self._issue(
                    issues,
                    "Error",
                    "Deck packing",
                    "Standard Back",
                    "Standard-back decks must be completely full.",
                )

            if plan["total_cards"] != total_copies:
                self._issue(
                    issues,
                    "Error",
                    "Deck plan",
                    "",
                    "Expanded card count does not match project quantities.",
                )

            for group in plan["groups"]:
                if sum(group["deck_sizes"]) != group["card_count"]:
                    self._issue(
                        issues,
                        "Error",
                        "Deck plan",
                        group["name"],
                        "Deck sizes do not add up to the group card count.",
                    )
                if any(size > options.deck_capacity for size in group["deck_sizes"]):
                    self._issue(
                        issues,
                        "Error",
                        "Deck capacity",
                        group["name"],
                        "A generated deck exceeds the selected capacity.",
                    )

        return self._result(
            issues,
            total_copies,
            plan,
            len(checked_paths),
        )

    @staticmethod
    def _result(
        issues: list[PreflightIssue],
        total_cards: int,
        plan: dict | None,
        unique_files_checked: int,
    ) -> dict:
        errors = sum(issue.severity == "Error" for issue in issues)
        warnings = sum(issue.severity == "Warning" for issue in issues)
        return {
            "passed": errors == 0,
            "errors": errors,
            "warnings": warnings,
            "issues": issues,
            "total_cards": total_cards,
            "unique_files_checked": unique_files_checked,
            "plan": plan,
        }
