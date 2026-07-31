from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.artwork_index_service import ArtworkIndexService



def move_to_recycle_bin(path: Path) -> None:
    """Move a file to the Windows Recycle Bin without external packages."""
    import ctypes
    from ctypes import wintypes

    if not path.exists():
        raise FileNotFoundError(path)

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x0040
    FOF_NOCONFIRMATION = 0x0010
    FOF_SILENT = 0x0004
    FOF_NOERRORUI = 0x0400

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    source = str(path.resolve()) + "\0\0"
    operation = SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = FO_DELETE
    operation.pFrom = source
    operation.pTo = None
    operation.fFlags = (
        FOF_ALLOWUNDO
        | FOF_NOCONFIRMATION
        | FOF_SILENT
        | FOF_NOERRORUI
    )

    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0:
        raise OSError(
            result,
            f"Windows could not move the file to the Recycle Bin: {path}",
        )
    if operation.fAnyOperationsAborted:
        raise RuntimeError(f"Recycle Bin operation was cancelled: {path}")


class SortableTableWidgetItem(QTableWidgetItem):
    """A table item that sorts using an optional numeric sort value."""

    def __init__(self, text: str, sort_value: int | float | str | None = None):
        super().__init__(text)
        self.sort_value = text.casefold() if sort_value is None else sort_value

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, SortableTableWidgetItem):
            return self.sort_value < other.sort_value
        return super().__lt__(other)


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

    def __init__(
        self,
        service: ArtworkIndexService,
        artwork_root_getter,
        library_kind: str = "original",
        parent=None,
    ):
        super().__init__(parent)
        self.service = service
        self.artwork_root_getter = artwork_root_getter
        self.library_kind = library_kind
        self.thread: QThread | None = None
        self.worker: IndexWorker | None = None
        self._records = []
        self._pipeline_filter: str | None = None
        self._build_ui()
        self._reload_extensions()
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

        self.clear_pipeline_button = QPushButton("Clear Pipeline Filter")
        self.clear_pipeline_button.setVisible(False)
        self.clear_pipeline_button.clicked.connect(self.clear_pipeline_filter)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setToolTip("Reload results from the artwork database")
        self.refresh_button.clicked.connect(self.refresh_results)

        self.duplicates_button = QPushButton("Duplicates")
        self.duplicates_button.setToolTip(
            "Show files with identical image contents (matching SHA-256)"
        )
        self.duplicates_button.clicked.connect(self.show_duplicates)

        self.index_button = QPushButton(
            "Index Originals"
            if self.library_kind == "original"
            else "Index Processed"
        )
        self.index_button.clicked.connect(self.start_index)

        controls.addWidget(QLabel("Search"))
        controls.addWidget(self.search_edit, 1)
        controls.addWidget(QLabel("Type"))
        controls.addWidget(self.extension_combo)
        controls.addWidget(self.search_button)
        controls.addWidget(self.clear_pipeline_button)
        controls.addWidget(self.refresh_button)
        controls.addWidget(self.duplicates_button)
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
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 280)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 75)
        self.table.setColumnWidth(4, 100)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.itemSelectionChanged.connect(self._show_selected)
        self.table.itemDoubleClicked.connect(lambda _: self.open_selected())

        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        self.preview_label = QLabel("Select an indexed image")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(280, 380)
        self.preview_label.setStyleSheet(
            "border: 1px solid palette(mid); padding: 8px;"
        )
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
            QMessageBox.warning(
                self,
                "Artwork folder required",
                "Choose an artwork folder in Project Configuration first.",
            )
            return

        root = Path(root_text)
        if not root.is_dir():
            QMessageBox.warning(
                self,
                "Artwork folder missing",
                f"Folder not found:\n{root}",
            )
            return

        self.index_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
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
        self.thread.finished.connect(self._index_thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def _index_thread_finished(self) -> None:
        # QThread and IndexWorker are single-use Qt objects. Clear the Python
        # references after Qt has finished with them so a new indexing run can
        # create a fresh thread and worker.
        self.worker = None
        self.thread = None

    def _index_progress(self, current: int, total: int, current_file: str) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(current)
        self.progress.setFormat(
            f"{current:,} / {total:,} — {Path(current_file).name}"
        )

    def _index_finished(self, summary) -> None:
        self.index_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
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
        self.refresh_button.setEnabled(True)
        self.progress.setVisible(False)
        self.status_message.emit("Artwork indexing failed")
        QMessageBox.critical(self, "Artwork indexing failed", message)

    def set_pipeline_filter(self, status: str) -> None:
        self._pipeline_filter = status
        self.clear_pipeline_button.setText(f"Clear: {status.replace('_', ' ').title()}")
        self.clear_pipeline_button.setVisible(True)
        self.refresh_results()

    def clear_pipeline_filter(self) -> None:
        self._pipeline_filter = None
        self.clear_pipeline_button.setVisible(False)
        self.refresh_results()

    def refresh_results(self) -> None:
        if self._pipeline_filter:
            result = self.service.pipeline_records(self._pipeline_filter)
        else:
            result = self.service.search(
                query=self.search_edit.text(),
                extension=self.extension_combo.currentText(),
                limit=500,
                library_kind=self.library_kind,
            )
        self._records = result.records

        sorting_enabled = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(len(self._records))

        for row, record in enumerate(self._records):
            folder = str(Path(record.relative_path).parent)
            if folder == ".":
                folder = ""

            items = [
                SortableTableWidgetItem(record.filename),
                SortableTableWidgetItem(folder),
                SortableTableWidgetItem(
                    record.resolution,
                    record.width * record.height if record.readable else -1,
                ),
                SortableTableWidgetItem(record.extension.lstrip(".").upper()),
                SortableTableWidgetItem(
                    self._format_bytes(record.file_size), record.file_size
                ),
            ]
            for column, item in enumerate(items):
                item.setData(Qt.ItemDataRole.UserRole, row)
                if not record.readable:
                    item.setToolTip("Image could not be read during indexing")
                self.table.setItem(row, column, item)

        self.table.setSortingEnabled(sorting_enabled)

        statistics = self.service.statistics(self.library_kind)
        shown = len(self._records)
        suffix = " (first 500 shown)" if result.total_matches > shown else ""
        filter_note = (
            f"Pipeline: {self._pipeline_filter.replace('_', ' ').title()}  •  "
            if self._pipeline_filter else ""
        )
        self.result_label.setText(
            filter_note + f"{result.total_matches:,} match(es){suffix}  •  "
            f"{statistics['total']:,} indexed  •  "
            f"{statistics['readable']:,} readable  •  "
            f"{statistics['unreadable']:,} unreadable  •  "
            f"{statistics['duplicate_files']:,} duplicate file(s) "
            f"in {statistics['duplicate_groups']:,} group(s)"
        )
        self.duplicates_button.setText(
            f"Duplicates ({statistics['duplicate_files']:,})"
        )
        self.duplicates_button.setEnabled(statistics["duplicate_files"] > 0)

        if not self._records:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("No indexed images found")
            self.details_label.clear()


    def show_duplicates(self) -> None:
        """Show every duplicate group and its full filesystem locations."""
        groups = self.service.duplicate_groups(
            self.library_kind,
            limit=10_000,
        )
        if not groups:
            QMessageBox.information(
                self,
                "Duplicate artwork",
                "No duplicate artwork files were found in this library.",
            )
            return

        root = Path(self.artwork_root_getter())
        dialog = QDialog(self)
        dialog.setWindowTitle(
            "Duplicate Original Artwork"
            if self.library_kind == "original"
            else "Duplicate Processed Artwork"
        )
        dialog.resize(900, 600)
        layout = QVBoxLayout(dialog)

        duplicate_files = sum(int(group["file_count"]) - 1 for group in groups)
        total_files = sum(int(group["file_count"]) for group in groups)
        summary = QLabel(
            f"{duplicate_files:,} extra duplicate file(s) across "
            f"{len(groups):,} group(s) ({total_files:,} files shown). "
            "Files in the same group have identical SHA-256 hashes."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        tree = QTreeWidget()
        tree.setColumnCount(2)
        tree.setHeaderLabels(["Duplicate group / filename", "Location"])
        tree.setAlternatingRowColors(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        tree.header().setStretchLastSection(True)
        tree.setColumnWidth(0, 300)

        for group_number, group in enumerate(groups, 1):
            relative_paths = list(group["relative_paths"])
            group_item = QTreeWidgetItem(
                [
                    f"Group {group_number} — {len(relative_paths)} identical files",
                    f"SHA-256: {str(group['sha256'])[:20]}…",
                ]
            )
            group_item.setExpanded(True)
            tree.addTopLevelItem(group_item)

            for relative_path in relative_paths:
                full_path = root / relative_path
                file_item = QTreeWidgetItem(
                    [full_path.name, str(full_path.parent)]
                )
                file_item.setData(0, Qt.ItemDataRole.UserRole, str(full_path))
                file_item.setToolTip(0, str(full_path))
                file_item.setToolTip(1, str(full_path))
                group_item.addChild(file_item)

        layout.addWidget(tree, 1)

        button_row = QHBoxLayout()
        open_image_button = QPushButton("Open Image")
        open_folder_button = QPushButton("Open Folder")
        delete_button = QPushButton("Delete Selected")
        close_button = QPushButton("Close")
        open_image_button.setEnabled(False)
        open_folder_button.setEnabled(False)
        delete_button.setEnabled(False)

        def selected_path() -> Path | None:
            item = tree.currentItem()
            if item is None:
                return None
            value = item.data(0, Qt.ItemDataRole.UserRole)
            return Path(value) if value else None

        def update_buttons() -> None:
            has_file = selected_path() is not None
            open_image_button.setEnabled(has_file)
            open_folder_button.setEnabled(has_file)
            delete_button.setEnabled(has_file)

        def open_image() -> None:
            path = selected_path()
            if path is not None:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

        def open_folder() -> None:
            path = selected_path()
            if path is not None:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


        def delete_selected() -> None:
            path = selected_path()
            if path is None:
                return
            if QMessageBox.question(
                dialog,
                "Delete duplicate",
                f"Move this file to the Recycle Bin?\n\n{path}",
            ) != QMessageBox.StandardButton.Yes:
                return
            try:
                move_to_recycle_bin(path)
                item = tree.currentItem()
                parent = item.parent()
                if parent:
                    parent.removeChild(item)
                self.status_message.emit(f"Moved to Recycle Bin: {path.name}")
                self.refresh_results()
            except Exception as exc:
                QMessageBox.critical(dialog,"Delete failed",str(exc))


        tree.currentItemChanged.connect(lambda *_: update_buttons())
        tree.itemDoubleClicked.connect(lambda *_: open_image())
        open_image_button.clicked.connect(open_image)
        open_folder_button.clicked.connect(open_folder)
        delete_button.clicked.connect(delete_selected)
        close_button.clicked.connect(dialog.accept)

        button_row.addWidget(open_image_button)
        button_row.addWidget(open_folder_button)
        button_row.addWidget(delete_button)
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        dialog.exec()

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

    def _selected_record(self):
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return None
        item = self.table.item(selected[0].row(), 0)
        if item is None:
            return None
        record_index = item.data(Qt.ItemDataRole.UserRole)
        if record_index is None or not 0 <= record_index < len(self._records):
            return None
        return self._records[record_index]

    def _show_selected(self) -> None:
        record = self._selected_record()
        if record is None:
            return

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

        details = (
            f"<b>{record.filename}</b><br>"
            f"{record.relative_path}<br>"
            f"{record.resolution} · {self._format_bytes(record.file_size)}<br>"
        )
        details += (
            f"SHA-256: {record.sha256[:16]}…"
            if record.sha256
            else "Unreadable image"
        )
        self.details_label.setText(details)

    def open_selected(self) -> None:
        record = self._selected_record()
        if record is None:
            return
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
