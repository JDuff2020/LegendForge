from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QSpinBox,
    QVBoxLayout,
)


class SettingsDialog(QDialog):
    def __init__(self, current: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LegendForge Settings")
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)

        appearance = QGroupBox("Appearance")
        appearance_form = QFormLayout(appearance)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["System", "Light", "Dark"])
        self.theme_combo.setCurrentText(str(current.get("theme", "System")))
        appearance_form.addRow("Theme", self.theme_combo)
        layout.addWidget(appearance)

        behavior = QGroupBox("Project behavior")
        behavior_form = QFormLayout(behavior)
        self.restore_project = QCheckBox("Restore the last project at startup")
        self.restore_project.setChecked(bool(current.get("restore_last_project", True)))
        behavior_form.addRow(self.restore_project)

        self.recent_limit = QSpinBox()
        self.recent_limit.setRange(3, 15)
        self.recent_limit.setValue(int(current.get("recent_project_limit", 8)))
        behavior_form.addRow("Recent-project limit", self.recent_limit)
        layout.addWidget(behavior)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {
            "theme": self.theme_combo.currentText(),
            "restore_last_project": self.restore_project.isChecked(),
            "recent_project_limit": self.recent_limit.value(),
        }
