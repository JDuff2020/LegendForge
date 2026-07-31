import logging
from datetime import datetime
from pathlib import Path

def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{datetime.now():%Y-%m-%d}_Builder.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )
    logging.getLogger(__name__).info("Builder started")
