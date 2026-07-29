from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import QUrl

from app.services.artwork_index_service import ArtworkIndexService


class IndexWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
    self,
    service: ArtworkIndexService,
    root: Path,
    library_kind: str,
):
        super().__init__()
        self.service = service
        self.root = root
        self.library_kind = library_kind

    def run(self) -> None:
        try:
            summary = self.service.index_folder(
                self.root,
                lambda update: self.progress.emit(
                    update.processed, update.total, update.current_file
                ),
                library_kind=self.library_kind,
            )
            self.finished.emit(summary)
        except Exception as exc:
            self.failed.emit(str(exc))


class ArtworkBrowser(QWidget):
    status_message = Signal(str)

    def __init__(self, service: ArtworkIndexService, artwork_root_getter, library_kind="original", parent=None):
        super().__init__(parent)
        self.service = service
        self.artwork_root_getter = artwork_root_getter
        self.library_kind = library_kind
        self.thread: QThread | None = None
        self.worker: IndexWorker | None = None
        self._records = []
        self._build_ui()
        self.refresh_results()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search filename or folder…")
        self.search_edit.returnPressed.connect(self.refresh_results)
        self.extension_combo = QComboBox()
        self.extension_combo.addItem("All")
        self.extension_combo.currentTextChanged.connect(self.refresh_results)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.refresh_results)
        self.index_button = QPushButton("Index Originals" if self.library_kind == "original" else "Index Processed")
        self.index_button.clicked.connect(self.start_index)
        controls.addWidget(QLabel("Search"))
        controls.addWidget(self.search_edit, 1)
        controls.addWidget(QLabel("Type"))
        controls.addWidget(self.extension_combo)
        controls.addWidget(self.search_button)
        controls.addWidget(self.index_button)
        root_layout.addLayout(controls)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root_layout.addWidget(self.progress)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Filename", "Folder", "Resolution", "Format", "Size"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 280)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 75)
        self.table.setColumnWidth(4, 100)
        self.table.itemSelectionChanged.connect(self._show_selected)
        self.table.itemDoubleClicked.connect(lambda _: self.open_selected())

        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        self.preview_label = QLabel("Select an indexed image")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(280, 380)
        self.preview_label.setStyleSheet("border: 1px solid palette(mid); padding: 8px;")
        self.details_label = QLabel()
        self.details_label.setWordWrap(True)
        self.open_button = QPushButton("Open Image")
        self.open_button.clicked.connect(self.open_selected)
        preview_layout.addWidget(self.preview_label, 1)
        preview_layout.addWidget(self.details_label)
        preview_layout.addWidget(self.open_button)
        splitter.addWidget(self.table)
        splitter.addWidget(preview)
        splitter.setSizes([760, 340])
        root_layout.addWidget(splitter, 1)

        self.result_label = QLabel()
        root_layout.addWidget(self.result_label)

    def start_index(self) -> None:
        if self.thread and self.thread.isRunning():
            return
        root_text = self.artwork_root_getter().strip()
        if not root_text:
            QMessageBox.warning(self, "Artwork folder required", "Choose an artwork folder in Project Configuration first.")
            return
        root = Path(root_text)
        if not root.is_dir():
            QMessageBox.warning(self, "Artwork folder missing", f"Folder not found:\n{root}")
            return

        self.index_button.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status_message.emit("Indexing artwork…")
        self.thread = QThread(self)
        self.worker = IndexWorker(
    self.service,
    root,
    self.library_kind,
)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._index_progress)
        self.worker.finished.connect(self._index_finished)
        self.worker.failed.connect(self._index_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _index_progress(self, current: int, total: int, current_file: str) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(current)
        self.progress.setFormat(f"{current:,} / {total:,} — {Path(current_file).name}")

    def _index_finished(self, summary) -> None:
        self.index_button.setEnabled(True)
        self.progress.setVisible(False)
        self._reload_extensions()
        self.refresh_results()
        message = (
            f"Artwork index complete: {summary.added:,} added, "
            f"{summary.updated:,} updated, {summary.unchanged:,} unchanged, "
            f"{summary.removed:,} removed in {summary.elapsed_seconds:.2f}s."
        )
        self.status_message.emit(message)
        if summary.errors:
            QMessageBox.warning(
                self,
                "Index completed with warnings",
                message + f"\n\nUnreadable/errors: {len(summary.errors):,}",
            )

    def _index_failed(self, message: str) -> None:
        self.index_button.setEnabled(True)
        self.progress.setVisible(False)
        self.status_message.emit("Artwork indexing failed")
        QMessageBox.critical(self, "Artwork indexing failed", message)

    def refresh_results(self) -> None:
        result = self.service.search(
            query=self.search_edit.text(),
            extension=self.extension_combo.currentText(),
            limit=500,
            library_kind=self.library_kind,
        )
        self._records = result.records
        self.table.setRowCount(len(self._records))
        for row, record in enumerate(self._records):
            folder = str(Path(record.relative_path).parent)
            if folder == ".":
                folder = ""
            values = [
                record.filename,
                folder,
                record.resolution,
                record.extension.lstrip(".").upper(),
                self._format_bytes(record.file_size),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if not record.readable:
                    item.setToolTip("Image could not be read during indexing")
                self.table.setItem(row, column, item)
        shown = len(self._records)
        suffix = " (first 500 shown)" if result.total_matches > shown else ""
        self.result_label.setText(f"{result.total_matches:,} match(es){suffix}")
        if not self._records:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("No indexed images found")
            self.details_label.clear()

    def _reload_extensions(self) -> None:
        current = self.extension_combo.currentText()
        self.extension_combo.blockSignals(True)
        self.extension_combo.clear()
        self.extension_combo.addItem("All")
        for extension in self.service.extensions(self.library_kind):
            self.extension_combo.addItem(extension)
        index = self.extension_combo.findText(current)
        self.extension_combo.setCurrentIndex(max(0, index))
        self.extension_combo.blockSignals(False)

    def _show_selected(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return
        record = self._records[selected[0].row()]
        root = Path(self.artwork_root_getter())
        path = record.absolute_path(root)
        pixmap = QPixmap(str(path)) if record.readable else QPixmap()
        if not pixmap.isNull():
            self.preview_label.setText("")
            self.preview_label.setPixmap(
                pixmap.scaled(
                    self.preview_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Preview unavailable")
        self.details_label.setText(
            f"<b>{record.filename}</b><br>"
            f"{record.relative_path}<br>"
            f"{record.resolution} · {self._format_bytes(record.file_size)}<br>"
            f"SHA-256: {record.sha256[:16]}…" if record.sha256 else "Unreadable image"
        )

    def open_selected(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return
        record = self._records[selected[0].row()]
        path = record.absolute_path(Path(self.artwork_root_getter()))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @staticmethod
    def _format_bytes(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
        return f"{size} B"
