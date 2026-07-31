from __future__ import annotations
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from PIL import Image
from app.models.results import ArtworkSummary

SUPPORTED = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

class ArtworkService:
    def scan(self, folder: Path) -> ArtworkSummary:
        files = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED]
        ext = Counter(p.suffix.lower() for p in files)
        widths, heights = [], []
        unreadable = 0
        hashes: dict[str, list[Path]] = defaultdict(list)
        for p in files:
            try:
                with Image.open(p) as image:
                    image.verify()
                with Image.open(p) as image:
                    widths.append(image.width)
                    heights.append(image.height)
                h = sha256()
                with p.open("rb") as f:
                    for chunk in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(chunk)
                hashes[h.hexdigest()].append(p)
            except Exception:
                unreadable += 1
        return ArtworkSummary(
            total_images=len(files),
            by_extension=dict(sorted(ext.items())),
            readable_images=len(files) - unreadable,
            unreadable_images=unreadable,
            average_width=round(sum(widths) / len(widths)) if widths else 0,
            average_height=round(sum(heights) / len(heights)) if heights else 0,
            duplicate_content_groups=sum(1 for group in hashes.values() if len(group) > 1),
        )
