from __future__ import annotations
from dataclasses import asdict, dataclass
from pathlib import Path
import json

@dataclass
class Project:
    project_name: str = "Marvel Proxies"
    artwork_folder: str = r"D:\Artwork"
    processed_artwork_folder: str = r"D:\Artwork_Processed"
    inventory_workbook: str = ""
    pricing_workbook: str = ""
    output_folder: str = r"D:\Marvel Legendary Print Projects"
    optimization: str = "Balanced"
    ai_backend: str = "None"
    ai_executable: str = ""
    version: str = "0.2.0"

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Project":
        data = json.loads(path.read_text(encoding="utf-8"))
        # Backward compatibility: old projects used artwork_folder for processed art.
        if "processed_artwork_folder" not in data and data.get("artwork_folder"):
            old = str(data["artwork_folder"])
            if "processed" in old.lower():
                data["processed_artwork_folder"] = old
                data["artwork_folder"] = r"D:\Artwork"
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)
