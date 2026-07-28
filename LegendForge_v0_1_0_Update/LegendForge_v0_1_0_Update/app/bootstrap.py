from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from app.services.logging_service import configure_logging
from app.services.settings_service import SettingsService
from app.ui.main_window import MainWindow


def run() -> int:
    root = Path(__file__).resolve().parents[1]
    configure_logging(root / "logs")

    QCoreApplication.setOrganizationName("JDuff2020")
    QCoreApplication.setApplicationName("LegendForge")
    QCoreApplication.setApplicationVersion("0.1.0")

    app = QApplication(sys.argv)
    app.setApplicationDisplayName("LegendForge")

    settings = SettingsService(root / "config.json")
    window = MainWindow(root, settings)
    window.show()
    return app.exec()
