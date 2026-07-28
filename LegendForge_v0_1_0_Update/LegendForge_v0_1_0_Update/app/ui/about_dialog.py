from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About LegendForge")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        title = QLabel("LegendForge")
        title.setObjectName("aboutTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("Marvel Legendary Project Manager")
        subtitle.setObjectName("aboutSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        description = QLabel(
            "Version 0.1.0 — Foundation\n\n"
            "Project configuration, artwork and workbook validation, persistent "
            "workspace layout, recent projects, and application logging.\n\n"
            "Deck optimization and MPC project generation will be added in later milestones."
        )
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
