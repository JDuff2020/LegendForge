from __future__ import annotations

import csv
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MPCExportOptions:
    deck_capacity: int
    split_custom_backs: bool = True
    use_hardlinks: bool = False


class MPCExportService:
    """Create ordered MPC upload folders from a print project."""

    def expand_cards(self, items: list[dict], processed_root: Path) -> list[dict]:
        cards: list[dict] = []
        for item in items:
            front = processed_root / item["relative_path"]
            back_relative = item.get("effective_back_relative_path")
            if not back_relative:
                raise ValueError(f"Missing back for {item['filename']}")
            back = processed_root / back_relative
            if not front.is_file():
                raise FileNotFoundError(front)
            if not back.is_file():
                raise FileNotFoundError(back)
            for copy_number in range(1, int(item["quantity"]) + 1):
                cards.append(
                    {
                        "front": front,
                        "back": back,
                        "front_filename": item["filename"],
                        "front_relative_path": item["relative_path"],
                        "back_filename": item.get("effective_back_filename") or back.name,
                        "back_relative_path": back_relative,
                        "back_status": item.get("back_status") or "Default Back",
                        "copy_number": copy_number,
                    }
                )
        return cards

    @staticmethod
    def _chunks(cards: list[dict], capacity: int) -> list[list[dict]]:
        if capacity < 1:
            raise ValueError("Deck capacity must be at least 1.")
        return [cards[index:index + capacity] for index in range(0, len(cards), capacity)]

    def _split_for_export(
        self,
        cards: list[dict],
        options: MPCExportOptions,
    ) -> tuple[list[dict], list[dict], int, int]:
        """
        Split cards so every Standard_Back deck is completely full.

        Any standard-back remainder is transferred to Custom_Backs. Those
        transferred cards retain their standard effective back and are exported
        with an individually numbered back file beside the true custom-back
        cards.
        """
        if not options.split_custom_backs:
            return [], cards, 0, 0

        original_standard = [
            card for card in cards if card["back_status"] != "Custom Back"
        ]
        true_custom = [
            card for card in cards if card["back_status"] == "Custom Back"
        ]

        full_standard_count = (
            len(original_standard) // options.deck_capacity
        ) * options.deck_capacity
        standard = original_standard[:full_standard_count]
        transferred_standard = original_standard[full_standard_count:]
        custom = true_custom + transferred_standard

        return (
            standard,
            custom,
            len(true_custom),
            len(transferred_standard),
        )

    def plan(self, items: list[dict], processed_root: Path, options: MPCExportOptions) -> dict:
        cards = self.expand_cards(items, processed_root)
        (
            standard,
            custom,
            true_custom_count,
            transferred_standard_count,
        ) = self._split_for_export(cards, options)

        groups = []
        if standard:
            groups.append(
                self._group_plan(
                    "Standard_Back",
                    standard,
                    options.deck_capacity,
                    False,
                )
            )
        if custom:
            groups.append(
                self._group_plan(
                    "Custom_Backs",
                    custom,
                    options.deck_capacity,
                    True,
                )
            )

        return {
            "total_cards": len(cards),
            "standard_cards": len(standard),
            "custom_cards": len(custom),
            "true_custom_cards": true_custom_count,
            "transferred_standard_cards": transferred_standard_count,
            "deck_capacity": options.deck_capacity,
            "groups": groups,
            "total_decks": sum(group["deck_count"] for group in groups),
            "unused_slots": sum(group["unused_slots"] for group in groups),
        }

    @staticmethod
    def _group_plan(name: str, cards: list[dict], capacity: int, individual_backs: bool) -> dict:
        deck_count = (len(cards) + capacity - 1) // capacity
        return {
            "name": name,
            "card_count": len(cards),
            "deck_count": deck_count,
            "unused_slots": deck_count * capacity - len(cards),
            "individual_backs": individual_backs,
            "deck_sizes": [len(deck) for deck in MPCExportService._chunks(cards, capacity)],
        }

    @staticmethod
    def _safe_name(name: str) -> str:
        invalid = '<>:"/\\|?*'
        result = "".join("_" if char in invalid else char for char in name).strip()
        return result or "card"

    @staticmethod
    def _copy_or_link(source: Path, destination: Path, use_hardlinks: bool) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if use_hardlinks:
            try:
                os.link(source, destination)
                return
            except OSError:
                pass
        shutil.copy2(source, destination)

    def export(
        self,
        items: list[dict],
        processed_root: Path,
        output_folder: Path,
        options: MPCExportOptions,
        project_name: str,
        progress_callback=None,
    ) -> dict:
        plan = self.plan(items, processed_root, options)
        cards = self.expand_cards(items, processed_root)
        output_folder.mkdir(parents=True, exist_ok=True)

        if options.split_custom_backs:
            standard, custom, _, _ = self._split_for_export(cards, options)
            grouped_cards = [
                ("Standard_Back", standard, False),
                ("Custom_Backs", custom, True),
            ]
        else:
            grouped_cards = [("All_Cards", cards, True)]

        written_files = []
        completed_decks = 0
        total_decks = sum(len(self._chunks(group_cards, options.deck_capacity)) for _, group_cards, _ in grouped_cards if group_cards)

        for group_name, group_cards, individual_backs in grouped_cards:
            if not group_cards:
                continue
            for deck_number, deck_cards in enumerate(self._chunks(group_cards, options.deck_capacity), 1):
                deck_folder = output_folder / group_name / f"Deck_{deck_number:03d}_{len(deck_cards)}_cards"
                fronts_folder = deck_folder / "Fronts"
                backs_folder = deck_folder / "Backs"
                common_back_folder = deck_folder / "Back"
                manifest_rows = []

                if not individual_backs:
                    unique_backs = {card["back"].resolve() for card in deck_cards}
                    if len(unique_backs) != 1:
                        raise ValueError(
                            "A standard-back deck contains more than one effective back. "
                            "Use custom-back grouping or correct the project default back."
                        )
                    common_back = next(iter(unique_backs))
                    destination = common_back_folder / f"Back{common_back.suffix.lower()}"
                    self._copy_or_link(common_back, destination, options.use_hardlinks)
                    written_files.append(destination)

                for slot, card in enumerate(deck_cards, 1):
                    prefix = f"{slot:04d}"
                    front_destination = fronts_folder / (
                        f"{prefix}_{self._safe_name(Path(card['front_filename']).stem)}{card['front'].suffix.lower()}"
                    )
                    self._copy_or_link(card["front"], front_destination, options.use_hardlinks)
                    written_files.append(front_destination)

                    back_destination = ""
                    if individual_backs:
                        back_destination_path = backs_folder / (
                            f"{prefix}_{self._safe_name(Path(card['back_filename']).stem)}{card['back'].suffix.lower()}"
                        )
                        self._copy_or_link(card["back"], back_destination_path, options.use_hardlinks)
                        written_files.append(back_destination_path)
                        back_destination = str(back_destination_path.relative_to(deck_folder)).replace("\\", "/")

                    manifest_rows.append(
                        {
                            "Slot": slot,
                            "Front Upload File": str(front_destination.relative_to(deck_folder)).replace("\\", "/"),
                            "Back Upload File": back_destination or "Back/" + next(common_back_folder.iterdir()).name,
                            "Source Front": card["front_relative_path"],
                            "Source Back": card["back_relative_path"],
                            "Back Status": card["back_status"],
                        }
                    )

                manifest_path = deck_folder / "upload_manifest.csv"
                with manifest_path.open("w", newline="", encoding="utf-8-sig") as file:
                    writer = csv.DictWriter(file, fieldnames=list(manifest_rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(manifest_rows)
                written_files.append(manifest_path)

                instructions_path = deck_folder / "UPLOAD_INSTRUCTIONS.txt"
                if individual_backs:
                    instructions = (
                        "Upload every file in Fronts in filename order to the front slots.\n"
                        "Then upload every file in Backs in the same filename order to the matching back slots.\n"
                        "The numeric prefixes pair each front with its back.\n"
                    )
                else:
                    instructions = (
                        "Upload every file in Fronts in filename order.\n"
                        "Use the single image in Back as the common back for this deck.\n"
                    )
                instructions_path.write_text(instructions, encoding="utf-8")
                written_files.append(instructions_path)

                completed_decks += 1
                if progress_callback:
                    progress_callback(completed_decks, total_decks)

        summary_path = output_folder / "export_summary.txt"
        lines = [
            f"Project: {project_name}",
            f"Total cards: {plan['total_cards']}",
            f"Deck capacity: {plan['deck_capacity']}",
            f"Total decks: {plan['total_decks']}",
            f"Unused slots: {plan['unused_slots']}",
            f"Standard-back cards in full common-back decks: {plan['standard_cards']}",
            f"Cards in individually backed decks: {plan['custom_cards']}",
            f"True custom-back cards: {plan['true_custom_cards']}",
            f"Standard-back remainder moved to custom decks: "
            f"{plan['transferred_standard_cards']}",
            "",
        ]
        for group in plan["groups"]:
            lines.append(
                f"{group['name']}: {group['card_count']} cards, "
                f"{group['deck_count']} decks, {group['unused_slots']} unused slots"
            )
        summary_path.write_text("\n".join(lines), encoding="utf-8")
        written_files.append(summary_path)

        return {**plan, "files": written_files, "output_folder": output_folder}
