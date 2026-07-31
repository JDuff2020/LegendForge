from __future__ import annotations

import json
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QToolBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.models.project import Project
from app.services.settings_service import SettingsService
from app.services.database_service import DatabaseService
from app.services.artwork_index_service import ArtworkIndexService
from app.services.validation_service import ValidationService
from app.services.print_project_service import PrintProjectService
from app.ui.about_dialog import AboutDialog
from app.ui.artwork_browser import ArtworkBrowser
from app.ui.settings_dialog import SettingsDialog
from app.ui.pipeline_dashboard import PipelineDashboard
from app.ui.print_project_builder import PrintProjectBuilder

log = logging.getLogger(__name__)


class ValidationWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, project: Project):
        super().__init__()
        self.project = project

    def run(self) -> None:
        try:
            self.finished.emit(ValidationService().validate(self.project))
        except Exception as exc:  # defensive boundary around background work
            log.exception("Project validation failed")
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    APP_TITLE = "LegendForge"
    APP_SUBTITLE = "Marvel Legendary Project Manager"
    APP_VERSION = "0.2.0-revised"

    def __init__(self, root: Path, settings: SettingsService):
        super().__init__()
        self.root = root
        self.settings = settings
        self.project_path: Path | None = None
        self.thread: QThread | None = None
        self.worker: ValidationWorker | None = None

        self.database = DatabaseService(root / "data" / "legendforge.sqlite3")
        self.artwork_index = ArtworkIndexService(self.database)
        self.print_projects = PrintProjectService(self.database)

        default_pricing = root / "resources" / "DeckPrices.xlsx"
        self.project = Project(pricing_workbook=str(default_pricing))

        self.setWindowTitle(f"{self.APP_TITLE} {self.APP_VERSION}")
        self.resize(1120, 790)
        self.setMinimumSize(900, 650)

        self._build_actions()
        self._build_menu()
        self._build_toolbar()
        self._build_central_widget()
        self._build_docks()
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

        self._apply_theme(str(self.settings.get("theme", "System")))
        self._restore_workspace()
        self._load_last_project()
        self._refresh_recent_projects()
        self._project_to_fields()

    # ---------- UI construction ----------
    def _build_actions(self) -> None:
        self.new_action = QAction("New Project", self)
        self.new_action.setShortcut(QKeySequence.StandardKey.New)
        self.new_action.triggered.connect(self.new_project)

        self.open_action = QAction("Open Project…", self)
        self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.open_action.triggered.connect(self.open_project)

        self.save_action = QAction("Save Project", self)
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.triggered.connect(self.save_project)

        self.save_as_action = QAction("Save Project As…", self)
        self.save_as_action.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.save_as_action.triggered.connect(self.save_project_as)

        self.validate_action = QAction("Validate Project", self)
        self.validate_action.setShortcut("F5")
        self.validate_action.triggered.connect(self.validate_project)

        self.settings_action = QAction("Settings…", self)
        self.settings_action.triggered.connect(self.show_settings)

        self.reset_layout_action = QAction("Reset Workspace Layout", self)
        self.reset_layout_action.triggered.connect(self.reset_workspace_layout)

        self.about_action = QAction("About LegendForge", self)
        self.about_action.triggered.connect(self.show_about)

        self.exit_action = QAction("Exit", self)
        self.exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        self.exit_action.triggered.connect(self.close)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addActions([self.new_action, self.open_action])
        self.recent_menu = file_menu.addMenu("Open Recent")
        file_menu.addSeparator()
        file_menu.addActions([self.save_action, self.save_as_action])
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)

        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addAction(self.settings_action)

        view_menu = self.menuBar().addMenu("&View")
        self.docks_menu = view_menu.addMenu("Panels")
        view_menu.addAction(self.reset_layout_action)

        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction(self.validate_action)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self.about_action)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main Toolbar", self)
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(True)
        toolbar.addActions([self.new_action, self.open_action, self.save_action])
        toolbar.addSeparator()
        toolbar.addAction(self.validate_action)
        toolbar.addSeparator()
        toolbar.addAction(self.settings_action)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

    def _build_central_widget(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 16, 18, 16)

        title = QLabel(self.APP_TITLE)
        title.setObjectName("title")
        subtitle = QLabel(f"{self.APP_SUBTITLE} — Foundation milestone")
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        project_box = QGroupBox("Project Configuration")
        form = QGridLayout(project_box)
        self.name_edit = QLineEdit()
        self.artwork_edit = QLineEdit()
        self.processed_artwork_edit = QLineEdit()
        self.inventory_edit = QLineEdit()
        self.pricing_edit = QLineEdit()
        self.output_edit = QLineEdit()
        self.optimization_combo = QComboBox()
        self.optimization_combo.addItems(["Balanced", "Lowest Cost", "Fewest Decks"])

        fields = [
            ("Project name", self.name_edit, None),
            ("Original artwork", self.artwork_edit, lambda: self._browse_folder(self.artwork_edit)),
            ("Processed artwork", self.processed_artwork_edit, lambda: self._browse_folder(self.processed_artwork_edit)),
            ("Inventory workbook", self.inventory_edit, lambda: self._browse_file(self.inventory_edit)),
            ("Pricing workbook", self.pricing_edit, lambda: self._browse_file(self.pricing_edit)),
            ("Output folder", self.output_edit, lambda: self._browse_folder(self.output_edit)),
        ]
        for row, (label, widget, callback) in enumerate(fields):
            form.addWidget(QLabel(label), row, 0)
            form.addWidget(widget, row, 1)
            if callback:
                browse = QPushButton("Browse…")
                browse.clicked.connect(callback)
                form.addWidget(browse, row, 2)

        form.addWidget(QLabel("Optimization mode"), len(fields), 0)
        form.addWidget(self.optimization_combo, len(fields), 1)
        layout.addWidget(project_box)

        buttons = QHBoxLayout()
        self.validate_btn = QPushButton("Validate Project")
        self.validate_btn.clicked.connect(self.validate_project)
        self.save_btn = QPushButton("Save Project")
        self.save_btn.clicked.connect(self.save_project)
        self.build_btn = QPushButton("Build Project — coming in a later milestone")
        self.build_btn.setEnabled(False)
        buttons.addWidget(self.validate_btn)
        buttons.addWidget(self.save_btn)
        buttons.addStretch()
        buttons.addWidget(self.build_btn)
        layout.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        summary_box = QGroupBox("Project Summary")
        summary_layout = QVBoxLayout(summary_box)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setPlaceholderText(
            "Press F5 or choose Tools → Validate Project to display artwork, inventory, and pricing statistics."
        )
        summary_layout.addWidget(self.summary)
        layout.addWidget(summary_box, 1)

        self.tabs.addTab(central, "Project")

        self.artwork_browser = ArtworkBrowser(
            service=self.artwork_index,
            artwork_root_getter=lambda: self.artwork_edit.text(),
            library_kind="original",
            parent=self,
        )
        self.artwork_browser.status_message.connect(self.statusBar().showMessage)
        self.tabs.addTab(self.artwork_browser, "Original Artwork")

        self.processed_browser = ArtworkBrowser(
            service=self.artwork_index,
            artwork_root_getter=lambda: self.processed_artwork_edit.text(),
            library_kind="processed",
            parent=self,
        )
        self.processed_browser.status_message.connect(self.statusBar().showMessage)
        self.tabs.addTab(self.processed_browser, "Processed Artwork")

        self.pipeline_dashboard = PipelineDashboard(
            self.artwork_index,
            self._get_ai_backend,
            self._set_ai_backend,
            lambda: self.artwork_edit.text(),
            lambda: self.processed_artwork_edit.text(),
            parent=self,
        )
        self.pipeline_dashboard.status_message.connect(self.statusBar().showMessage)
        self.tabs.addTab(self.pipeline_dashboard, "Pipeline / AI")

        self.print_builder = PrintProjectBuilder(
            self.print_projects,
            lambda: self.processed_artwork_edit.text(),
            lambda: self.output_edit.text(),
            lambda: self.pricing_edit.text(),
            parent=self,
        )
        self.print_builder.status_message.connect(self.statusBar().showMessage)
        self.tabs.addTab(self.print_builder, "Print Projects")

        self.setCentralWidget(self.tabs)

    def _build_docks(self) -> None:
        self.recent_dock = QDockWidget("Recent Projects", self)
        self.recent_dock.setObjectName("recentProjectsDock")
        self.recent_list = QListWidget()
        self.recent_list.itemDoubleClicked.connect(
            lambda item: self._open_project_path(Path(item.data(Qt.ItemDataRole.UserRole)))
        )
        self.recent_dock.setWidget(self.recent_list)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.recent_dock)

        self.log_dock = QDockWidget("Session Notes", self)
        self.log_dock.setObjectName("sessionNotesDock")
        self.session_notes = QPlainTextEdit()
        self.session_notes.setReadOnly(True)
        self.session_notes.setPlainText(
            "LegendForge is ready.\n\n"
            "This milestone adds the SQLite database engine, incremental artwork indexing, fast search, and previews."
        )
        self.log_dock.setWidget(self.session_notes)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.log_dock)

        self.docks_menu.addAction(self.recent_dock.toggleViewAction())
        self.docks_menu.addAction(self.log_dock.toggleViewAction())

    # ---------- Project operations ----------
    def new_project(self) -> None:
        self.project_path = None
        self.project = Project(pricing_workbook=str(self.root / "resources" / "DeckPrices.xlsx"))
        self._project_to_fields()
        self.summary.clear()
        self.setWindowTitle(f"{self.APP_TITLE} {self.APP_VERSION} — Untitled")
        self.statusBar().showMessage("Created a new project")

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open project",
            str(self.root / "projects"),
            "LegendForge Project (*.json)",
        )
        if path:
            self._open_project_path(Path(path))

    def _open_project_path(self, path: Path) -> None:
        try:
            self.project = Project.load(path)
            self.project_path = path
            self._project_to_fields()
            self._remember_project(path)
            self.setWindowTitle(f"{self.APP_TITLE} {self.APP_VERSION} — {path.stem}")
            self.statusBar().showMessage(f"Opened {path.name}")
            log.info("Opened project %s", path)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", str(exc))

    def save_project(self) -> None:
        if self.project_path is None:
            self.save_project_as()
            return
        try:
            self._fields_to_project()
            self.project.save(self.project_path)
            self._remember_project(self.project_path)
            self.setWindowTitle(
                f"{self.APP_TITLE} {self.APP_VERSION} — {self.project_path.stem}"
            )
            self.statusBar().showMessage(f"Saved {self.project_path.name}")
            log.info("Saved project %s", self.project_path)
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))

    def save_project_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save project",
            str(self.root / "projects" / "Marvel_Proxies.json"),
            "LegendForge Project (*.json)",
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        self.project_path = Path(path)
        self.save_project()

    def _fields_to_project(self) -> None:
        self.project = Project(
            project_name=self.name_edit.text().strip() or "Marvel Proxies",
            artwork_folder=self.artwork_edit.text().strip(),
            processed_artwork_folder=self.processed_artwork_edit.text().strip(),
            inventory_workbook=self.inventory_edit.text().strip(),
            pricing_workbook=self.pricing_edit.text().strip(),
            output_folder=self.output_edit.text().strip(),
            optimization=self.optimization_combo.currentText(),
            version="1.0",
        )

    def _project_to_fields(self) -> None:
        p = self.project
        self.name_edit.setText(p.project_name)
        self.artwork_edit.setText(p.artwork_folder)
        self.processed_artwork_edit.setText(p.processed_artwork_folder)
        self.inventory_edit.setText(p.inventory_workbook)
        self.pricing_edit.setText(p.pricing_workbook)
        self.output_edit.setText(p.output_folder)
        self.optimization_combo.setCurrentText(p.optimization)

    # ---------- Validation ----------
    def validate_project(self) -> None:
        if self.thread and self.thread.isRunning():
            return
        self._fields_to_project()
        self.validate_btn.setEnabled(False)
        self.validate_action.setEnabled(False)
        self.progress.setRange(0, 0)
        self.statusBar().showMessage("Validating project…")

        self.thread = QThread(self)
        self.worker = ValidationWorker(self.project)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._validation_finished)
        self.worker.failed.connect(self._validation_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _validation_finished(self, report) -> None:
        self._finish_validation_ui(True)
        lines = ["VALIDATION", "=" * 72]
        for item in report.items:
            lines.append(f"{item.status:8}  {item.label}: {item.message}")
        if report.artwork:
            a = report.artwork
            lines += [
                "",
                "ARTWORK",
                f"Images: {a.total_images:,}",
                f"Readable: {a.readable_images:,}",
                f"Average resolution: {a.average_width} × {a.average_height}",
                f"Extensions: {json.dumps(a.by_extension, sort_keys=True)}",
                f"Duplicate content groups: {a.duplicate_content_groups}",
            ]
        if report.pricing:
            p = report.pricing
            lines += [
                "",
                "PRICING",
                f"Sheet: {p.source_sheet}",
                f"Deck sizes ({len(p.deck_sizes)}): {', '.join(map(str, p.deck_sizes))}",
                f"Quantity tiers ({len(p.quantity_tiers)}): {', '.join(p.quantity_tiers)}",
            ]
        if report.inventory:
            i = report.inventory
            lines += [
                "",
                "INVENTORY",
                f"Sheets: {', '.join(i.sheets)}",
                f"Data rows: {i.total_rows:,}",
                f"Estimated cards: {i.estimated_cards:,}",
            ]
            if i.categories:
                lines.append(
                    "Categories: "
                    + ", ".join(f"{k}={v:,}" for k, v in sorted(i.categories.items()))
                )
        lines += ["", "RESULT", "READY" if report.ready else "NEEDS ATTENTION"]
        self.summary.setPlainText("\n".join(lines))
        self.session_notes.appendPlainText(
            f"\nValidation complete — {'ready' if report.ready else 'needs attention'}."
        )
        log.info("Validation complete; ready=%s", report.ready)

    def _validation_failed(self, message: str) -> None:
        self._finish_validation_ui(False)
        QMessageBox.critical(self, "Validation failed", message)

    def _finish_validation_ui(self, success: bool) -> None:
        self.validate_btn.setEnabled(True)
        self.validate_action.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(1 if success else 0)
        self.statusBar().showMessage("Validation complete" if success else "Validation failed")

    # ---------- Settings and layout ----------
    def show_settings(self) -> None:
        dialog = SettingsDialog(self.settings.data, self)
        if dialog.exec():
            values = dialog.values()
            self.settings.update(values)
            self._apply_theme(values["theme"])
            self._refresh_recent_projects()
            self.statusBar().showMessage("Settings saved")

    def show_about(self) -> None:
        AboutDialog(self).exec()

    def _apply_theme(self, theme: str) -> None:
        if theme == "Dark":
            stylesheet = """
                QMainWindow, QWidget { background: #20252b; color: #eef1f4; }
                QLabel#title { font-size: 28px; font-weight: 700; }
                QLabel#subtitle { color: #aeb8c2; margin-bottom: 8px; }
                QGroupBox { border: 1px solid #49525c; border-radius: 8px; margin-top: 12px; padding: 14px; font-weight: 600; }
                QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
                QLineEdit, QComboBox, QPlainTextEdit, QListWidget { background: #2a3037; border: 1px solid #59636e; border-radius: 5px; padding: 7px; color: #eef1f4; }
                QPushButton { padding: 8px 14px; border-radius: 5px; border: 1px solid #68737e; background: #303840; }
                QPushButton:hover { background: #3a4650; }
                QPushButton:disabled { color: #7f8993; background: #292f35; }
                QDockWidget::title { padding: 6px; }
                QLabel#aboutTitle { font-size: 26px; font-weight: 700; }
                QLabel#aboutSubtitle { color: #aeb8c2; }
            """
        elif theme == "Light":
            stylesheet = """
                QMainWindow, QWidget { background: #f4f6f8; color: #18212b; }
                QLabel#title { font-size: 28px; font-weight: 700; color: #18212b; }
                QLabel#subtitle { color: #5f6b76; margin-bottom: 8px; }
                QGroupBox { background: white; border: 1px solid #d7dde3; border-radius: 8px; margin-top: 12px; padding: 14px; font-weight: 600; }
                QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
                QLineEdit, QComboBox, QPlainTextEdit, QListWidget { border: 1px solid #bcc6d0; border-radius: 5px; padding: 7px; background: white; }
                QPushButton { padding: 8px 14px; border-radius: 5px; border: 1px solid #9aa8b5; background: #ffffff; }
                QPushButton:hover { background: #edf3f8; }
                QPushButton:disabled { color: #89939d; background: #e6eaee; }
                QLabel#aboutTitle { font-size: 26px; font-weight: 700; }
                QLabel#aboutSubtitle { color: #5f6b76; }
            """
        else:
            stylesheet = """
                QLabel#title { font-size: 28px; font-weight: 700; }
                QLabel#subtitle { color: palette(mid); margin-bottom: 8px; }
                QGroupBox { border: 1px solid palette(mid); border-radius: 8px; margin-top: 12px; padding: 14px; font-weight: 600; }
                QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
                QLineEdit, QComboBox, QPlainTextEdit, QListWidget { border: 1px solid palette(mid); border-radius: 5px; padding: 7px; }
                QPushButton { padding: 8px 14px; border-radius: 5px; }
                QLabel#aboutTitle { font-size: 26px; font-weight: 700; }
            """
        self.setStyleSheet(stylesheet)

    def reset_workspace_layout(self) -> None:
        self.removeDockWidget(self.recent_dock)
        self.removeDockWidget(self.log_dock)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.recent_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.log_dock)
        self.recent_dock.show()
        self.log_dock.show()
        self.statusBar().showMessage("Workspace layout reset")

    def _restore_workspace(self) -> None:
        geometry = self.settings.get("window_geometry")
        state = self.settings.get("window_state")
        try:
            if geometry:
                self.restoreGeometry(bytes.fromhex(geometry))
            if state:
                self.restoreState(bytes.fromhex(state))
        except ValueError:
            log.warning("Stored workspace state was invalid and was ignored")

    # ---------- Recent projects ----------
    def _remember_project(self, path: Path) -> None:
        normalized = str(path.resolve())
        recent = [p for p in self.settings.get("recent_projects", []) if p != normalized]
        recent.insert(0, normalized)
        limit = int(self.settings.get("recent_project_limit", 8))
        recent = recent[:limit]
        self.settings.update({"last_project": normalized, "recent_projects": recent})
        self._refresh_recent_projects()

    def _refresh_recent_projects(self) -> None:
        recent = [Path(p) for p in self.settings.get("recent_projects", [])]
        recent = [p for p in recent if p.is_file()]
        self.recent_menu.clear()
        self.recent_list.clear()
        if not recent:
            empty = QAction("No recent projects", self)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        for path in recent:
            action = QAction(path.name, self)
            action.setToolTip(str(path))
            action.triggered.connect(lambda checked=False, p=path: self._open_project_path(p))
            self.recent_menu.addAction(action)
            item_text = f"{path.stem}\n{path.parent}"
            self.recent_list.addItem(item_text)
            item = self.recent_list.item(self.recent_list.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, str(path))

    def _load_last_project(self) -> None:
        if not bool(self.settings.get("restore_last_project", True)):
            return
        path = self.settings.get("last_project")
        if path and Path(path).is_file():
            try:
                self.project_path = Path(path)
                self.project = Project.load(self.project_path)
                self.setWindowTitle(
                    f"{self.APP_TITLE} {self.APP_VERSION} — {self.project_path.stem}"
                )
            except Exception:
                log.exception("Could not restore last project")

    # ---------- File pickers and shutdown ----------
    def _browse_folder(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select folder", edit.text() or str(self.root)
        )
        if path:
            edit.setText(path)

    def _browse_file(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select workbook",
            edit.text() or str(self.root),
            "Excel workbooks (*.xlsx *.xlsm)",
        )
        if path:
            edit.setText(path)

    def _get_ai_backend(self):
        return self.project.ai_backend, self.project.ai_executable

    def _set_ai_backend(self, backend: str, executable: str):
        self._fields_to_project()
        self.project.ai_backend = backend
        self.project.ai_executable = executable
        self.statusBar().showMessage("AI backend settings updated")

    def closeEvent(self, event: QCloseEvent) -> None:
        self._fields_to_project()
        self.settings.update(
            {
                "window_geometry": bytes(self.saveGeometry()).hex(),
                "window_state": bytes(self.saveState()).hex(),
            }
        )
        event.accept()
