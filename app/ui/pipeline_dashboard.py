from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.processing import ProcessingPreset
from app.services.artwork_index_service import ArtworkIndexService
from app.services.artwork_processing_service import ArtworkProcessingService


class ProcessingWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, processor, records, original_root, processed_root, preset):
        super().__init__()
        self.processor = processor
        self.records = records
        self.original_root = original_root
        self.processed_root = processed_root
        self.preset = preset
        self.cancel_requested = False

    def cancel(self) -> None:
        self.cancel_requested = True

    def run(self) -> None:
        try:
            summary = self.processor.process_records(
                self.records,
                self.original_root,
                self.processed_root,
                self.preset,
                progress_callback=lambda current, total, name: self.progress.emit(
                    current, total, name
                ),
                cancel_callback=lambda: self.cancel_requested,
            )
            self.finished.emit(summary)
        except Exception as exc:
            self.failed.emit(str(exc))


class PipelineDashboard(QWidget):
    status_message = Signal(str)
    filter_requested = Signal(str)
    processing_finished = Signal()

    STATUS_ROWS = (
        ("TOTAL", "Originals indexed", False),
        ("READY", "Ready processed outputs", True),
        ("NEEDS_PROCESSING", "Needs processing", True),
        ("NEEDS_REBUILD", "Needs rebuild", True),
        ("UNREADABLE_ORIGINAL", "Unreadable originals", True),
        ("UNREADABLE_PROCESSED", "Unreadable processed outputs", True),
        ("ORPHANED_PROCESSED", "Orphaned processed outputs", True),
    )

    def __init__(
        self,
        service: ArtworkIndexService,
        backend_getter,
        backend_setter,
        original_root_getter,
        processed_root_getter,
        parent=None,
    ):
        super().__init__(parent)
        self.service = service
        self.processor = ArtworkProcessingService(service.database)
        self.backend_getter = backend_getter
        self.backend_setter = backend_setter
        self.original_root_getter = original_root_getter
        self.processed_root_getter = processed_root_getter
        self.count_labels: dict[str, QLabel] = {}
        self.view_buttons: dict[str, QPushButton] = {}
        self.thread: QThread | None = None
        self.worker: ProcessingWorker | None = None
        self._queue_records = []
        self._row_by_id: dict[int, int] = {}
        self._build()
        self._load_saved_presets()
        self.refresh()

    def _build(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer_layout.addWidget(scroll)

        content = QWidget()
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 12, 20, 20)
        layout.setSpacing(14)

        status_box = QGroupBox("Artwork Pipeline Status")
        status_layout = QVBoxLayout(status_box)
        status_layout.setContentsMargins(16, 18, 16, 14)
        status_layout.setSpacing(6)
        for status, label_text, clickable in self.STATUS_ROWS:
            row = QHBoxLayout()
            label = QLabel(label_text)
            count = QLabel("0")
            count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            count.setMinimumWidth(90)
            row.addWidget(label, 1)
            row.addWidget(count)
            self.count_labels[status] = count
            if clickable:
                button = QPushButton("View")
                button.clicked.connect(
                    lambda _=False, value=status: self.filter_requested.emit(value)
                )
                row.addWidget(button)
                self.view_buttons[status] = button
            status_layout.addLayout(row)
        layout.addWidget(status_box)

        preset_box = QGroupBox("Local Processing Preset")
        preset_layout = QVBoxLayout(preset_box)
        preset_layout.setContentsMargins(16, 18, 16, 16)
        saved_row = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.setEditable(False)
        self.preset_name = QLineEdit()
        self.preset_name.setPlaceholderText("Preset name")
        self.save_preset_button = QPushButton("Save Preset")
        self.delete_preset_button = QPushButton("Delete Preset")
        saved_row.addWidget(QLabel("Saved preset"))
        saved_row.addWidget(self.preset_combo, 1)
        saved_row.addWidget(self.preset_name)
        saved_row.addWidget(self.save_preset_button)
        saved_row.addWidget(self.delete_preset_button)
        preset_layout.addLayout(saved_row)

        preset_form = QFormLayout()
        preset_form.setHorizontalSpacing(18)
        preset_form.setVerticalSpacing(10)
        preset_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        preset_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 20000)
        self.width_spin.setValue(816)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 20000)
        self.height_spin.setValue(1110)
        self.format_combo = QComboBox()
        self.format_combo.addItems(["PNG", "JPEG", "WEBP"])
        self.fit_combo = QComboBox()
        self.fit_combo.addItems([
            "MPC bleed (edge extend)",
            "Minimum size (proportional)",
            "Contain",
            "Cover",
            "Stretch",
            "Original size",
        ])
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(1, 100)
        self.quality_spin.setValue(95)
        self.background_edit = QLineEdit("#000000")
        self.bleed_spin = QSpinBox()
        self.bleed_spin.setRange(0, 200)
        self.bleed_spin.setValue(32)
        self.bleed_spin.setToolTip(
            "Bleed on each side at the 816 × 1110 MPC minimum. "
            "LegendForge scales it automatically for larger outputs."
        )
        self.bleed_details = QLabel()
        self.bleed_details.setWordWrap(True)
        for widget in (
            self.width_spin, self.height_spin, self.format_combo, self.fit_combo,
            self.quality_spin, self.background_edit, self.bleed_spin,
        ):
            widget.setMinimumHeight(30)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        preset_form.addRow("Minimum / target width", self.width_spin)
        preset_form.addRow("Minimum / target height", self.height_spin)
        preset_form.addRow("Output format", self.format_combo)
        preset_form.addRow("Fit mode", self.fit_combo)
        preset_form.addRow("JPEG/WEBP quality", self.quality_spin)
        preset_form.addRow("Padding background", self.background_edit)
        preset_form.addRow("Bleed at 816 × 1110", self.bleed_spin)
        preset_form.addRow("Calculated MPC area", self.bleed_details)
        preset_layout.addLayout(preset_form)
        layout.addWidget(preset_box)

        queue_box = QGroupBox("Processing Queue and Review")
        queue_layout = QVBoxLayout(queue_box)
        queue_layout.setContentsMargins(16, 18, 16, 16)
        controls = QHBoxLayout()
        self.select_all_button = QPushButton("Select All")
        self.select_missing_button = QPushButton("Select Missing")
        self.select_rebuild_button = QPushButton("Select Rebuild")
        self.clear_selection_button = QPushButton("Clear")
        self.process_selected_button = QPushButton("Process Selected")
        self.retry_failed_button = QPushButton("Retry Failed")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        for button in (
            self.select_all_button, self.select_missing_button, self.select_rebuild_button,
            self.clear_selection_button, self.process_selected_button,
            self.retry_failed_button, self.cancel_button,
        ):
            controls.addWidget(button)
        controls.addStretch(1)
        queue_layout.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.queue_table = QTableWidget(0, 7)
        self.queue_table.setHorizontalHeaderLabels([
            "Use", "Filename", "Status", "Original", "Predicted output", "Scale", "Warning"
        ])
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.setColumnWidth(0, 48)
        self.queue_table.setColumnWidth(1, 260)
        self.queue_table.setColumnWidth(2, 120)
        self.queue_table.setColumnWidth(3, 110)
        self.queue_table.setColumnWidth(4, 130)
        self.queue_table.setColumnWidth(5, 90)
        self.queue_table.horizontalHeader().setStretchLastSection(True)
        self.queue_table.itemSelectionChanged.connect(self._update_preview)
        splitter.addWidget(self.queue_table)

        preview = QWidget()
        preview_layout = QVBoxLayout(preview)
        preview_layout.addWidget(QLabel("Original preview"))
        self.original_preview = QLabel("Select a queue item")
        self.original_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.original_preview.setMinimumSize(260, 320)
        self.original_preview.setStyleSheet("border: 1px solid palette(mid); padding: 6px;")
        preview_layout.addWidget(self.original_preview, 1)
        preview_layout.addWidget(QLabel("Processed output preview"))
        self.processed_preview = QLabel("Not processed yet")
        self.processed_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.processed_preview.setMinimumSize(260, 220)
        self.processed_preview.setStyleSheet("border: 1px solid palette(mid); padding: 6px;")
        preview_layout.addWidget(self.processed_preview, 1)
        self.preview_details = QLabel()
        self.preview_details.setWordWrap(True)
        preview_layout.addWidget(self.preview_details)
        open_row = QHBoxLayout()
        self.open_original_button = QPushButton("Open Original")
        self.open_processed_button = QPushButton("Open Processed")
        self.open_folder_button = QPushButton("Open Folder")
        open_row.addWidget(self.open_original_button)
        open_row.addWidget(self.open_processed_button)
        open_row.addWidget(self.open_folder_button)
        preview_layout.addLayout(open_row)
        splitter.addWidget(preview)
        splitter.setSizes([850, 360])
        queue_layout.addWidget(splitter, 1)

        self.processing_progress = QProgressBar()
        self.processing_progress.setVisible(False)
        self.processing_label = QLabel("No processing job running")
        queue_layout.addWidget(self.processing_progress)
        queue_layout.addWidget(self.processing_label)
        layout.addWidget(queue_box, 1)

        ai = QGroupBox("AI Enhancement Backend (future optional stage)")
        ai_form = QFormLayout(ai)
        self.backend = QComboBox()
        self.backend.addItems(["None", "External executable", "Real-ESRGAN (external)", "SwinIR (external)"])
        self.executable = QLineEdit()
        self.executable.setPlaceholderText("Path to AI enhancer executable or script")
        current, path = self.backend_getter()
        self.backend.setCurrentText(current)
        self.executable.setText(path)
        save = QPushButton("Save AI Backend Settings")
        save.clicked.connect(self._save_backend)
        ai_form.addRow("Backend", self.backend)
        ai_form.addRow("Executable", self.executable)
        ai_form.addRow("", save)
        layout.addWidget(ai)

        refresh = QPushButton("Refresh Pipeline Status and Queue")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)

        self.select_all_button.clicked.connect(lambda: self._select_by_status(None))
        self.select_missing_button.clicked.connect(lambda: self._select_by_status("NEEDS_PROCESSING"))
        self.select_rebuild_button.clicked.connect(lambda: self._select_by_status("NEEDS_REBUILD"))
        self.clear_selection_button.clicked.connect(lambda: self._select_by_status("CLEAR"))
        self.process_selected_button.clicked.connect(self.start_selected_processing)
        self.retry_failed_button.clicked.connect(self._retry_failed)
        self.cancel_button.clicked.connect(self.cancel_processing)
        self.open_original_button.clicked.connect(self._open_original)
        self.open_processed_button.clicked.connect(self._open_processed)
        self.open_folder_button.clicked.connect(self._open_folder)
        self.preset_combo.currentTextChanged.connect(self._apply_saved_preset)
        self.save_preset_button.clicked.connect(self._save_preset)
        self.delete_preset_button.clicked.connect(self._delete_preset)
        for widget in (
            self.width_spin, self.height_spin, self.format_combo, self.fit_combo,
            self.quality_spin, self.background_edit, self.bleed_spin,
        ):
            if hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(self._refresh_predictions)
            elif hasattr(widget, "currentTextChanged"):
                widget.currentTextChanged.connect(self._refresh_predictions)
            elif hasattr(widget, "textChanged"):
                widget.textChanged.connect(self._refresh_predictions)
        self._update_bleed_details()

    def _preset(self) -> ProcessingPreset:
        return ProcessingPreset(
            width=self.width_spin.value(),
            height=self.height_spin.value(),
            output_format=self.format_combo.currentText(),
            quality=self.quality_spin.value(),
            fit_mode=self.fit_combo.currentText(),
            background=self.background_edit.text().strip() or "#000000",
            bleed_px_at_minimum=self.bleed_spin.value(),
        )

    def _load_saved_presets(self) -> None:
        names = self.processor.saved_preset_names()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("Custom")
        self.preset_combo.addItems(names)
        self.preset_combo.blockSignals(False)
        self.delete_preset_button.setEnabled(False)

    def _save_preset(self) -> None:
        name = self.preset_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Preset name required", "Enter a name for the preset.")
            return
        self.processor.save_preset(name, self._preset())
        self._load_saved_presets()
        self.preset_combo.setCurrentText(name)
        self.status_message.emit(f"Saved processing preset: {name}")

    def _delete_preset(self) -> None:
        name = self.preset_combo.currentText()
        if name == "Custom":
            return
        self.processor.delete_preset(name)
        self._load_saved_presets()

    def _apply_saved_preset(self, name: str) -> None:
        self.delete_preset_button.setEnabled(name != "Custom")
        preset = self.processor.load_preset(name) if name != "Custom" else None
        if preset is None:
            return
        self.width_spin.setValue(preset.width)
        self.height_spin.setValue(preset.height)
        self.format_combo.setCurrentText(preset.output_format)
        self.quality_spin.setValue(preset.quality)
        self.fit_combo.setCurrentText(preset.fit_mode)
        self.background_edit.setText(preset.background)
        self.bleed_spin.setValue(preset.bleed_px_at_minimum)

    def _queue_items(self):
        records = []
        for status in ("NEEDS_PROCESSING", "NEEDS_REBUILD"):
            for record in self.service.pipeline_records(status).records:
                records.append(record)
        return records

    def _populate_queue(self) -> None:
        selected_ids = set(self._selected_ids())
        self._queue_records = self._queue_items()
        self._row_by_id.clear()
        self.queue_table.setRowCount(len(self._queue_records))
        preset = self._preset()
        for row, record in enumerate(self._queue_records):
            rid = int(record.id)
            self._row_by_id[rid] = row
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            check.setCheckState(Qt.CheckState.Checked if rid in selected_ids else Qt.CheckState.Unchecked)
            check.setData(Qt.ItemDataRole.UserRole, rid)
            predicted, scale = self.processor.predict_output(record.width, record.height, preset)
            warning = self.processor.scale_warning(scale)
            values = [
                check,
                QTableWidgetItem(record.filename),
                QTableWidgetItem(record.pipeline_status or ""),
                QTableWidgetItem(f"{record.width} × {record.height}"),
                QTableWidgetItem(f"{predicted[0]} × {predicted[1]}"),
                QTableWidgetItem(f"{scale * 100:.1f}%"),
                QTableWidgetItem(warning),
            ]
            for column, item in enumerate(values):
                item.setData(Qt.ItemDataRole.UserRole + 1, rid)
                self.queue_table.setItem(row, column, item)
        self.retry_failed_button.setEnabled(False)
        if self._queue_records and self.queue_table.currentRow() < 0:
            self.queue_table.selectRow(0)

    def _refresh_predictions(self, *_args) -> None:
        if not hasattr(self, "queue_table"):
            return
        preset = self._preset()
        self._update_bleed_details(preset)
        for row, record in enumerate(self._queue_records):
            predicted, scale = self.processor.predict_output(record.width, record.height, preset)
            self.queue_table.item(row, 4).setText(f"{predicted[0]} × {predicted[1]}")
            self.queue_table.item(row, 5).setText(f"{scale * 100:.1f}%")
            self.queue_table.item(row, 6).setText(self.processor.scale_warning(scale))
        self._update_preview()

    def _update_bleed_details(self, preset: ProcessingPreset | None = None) -> None:
        preset = preset or self._preset()
        if preset.fit_mode.casefold() != "mpc bleed (edge extend)":
            self.bleed_details.setText(
                "Bleed settings apply only to MPC bleed mode."
            )
            return
        bleed_x, bleed_y = self.processor.scaled_bleed(preset)
        trim_width, trim_height = self.processor.trim_dimensions(preset)
        self.bleed_details.setText(
            f"Final: {preset.width} × {preset.height} • "
            f"bleed: {bleed_x}px left/right and {bleed_y}px top/bottom • "
            f"trim: {trim_width} × {trim_height}"
        )

    def _selected_ids(self) -> list[int]:
        ids = []
        for row in range(self.queue_table.rowCount()):
            item = self.queue_table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                ids.append(int(item.data(Qt.ItemDataRole.UserRole)))
        return ids

    def _select_by_status(self, status: str | None) -> None:
        for row, record in enumerate(self._queue_records):
            checked = status is None or record.pipeline_status == status
            if status == "CLEAR":
                checked = False
            self.queue_table.item(row, 0).setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )

    def _current_record(self):
        row = self.queue_table.currentRow()
        if 0 <= row < len(self._queue_records):
            return self._queue_records[row]
        return None

    def _paths_for_record(self, record):
        original = record.absolute_path(Path(self.original_root_getter().strip()))
        processed = Path(self.processed_root_getter().strip()) / Path(record.relative_path).with_suffix(self._preset().extension)
        return original, processed

    def _set_preview_pixmap(self, label: QLabel, path: Path, empty_text: str) -> None:
        pixmap = QPixmap(str(path)) if path.is_file() else QPixmap()
        if pixmap.isNull():
            label.setPixmap(QPixmap())
            label.setText(empty_text)
        else:
            label.setText("")
            label.setPixmap(pixmap.scaled(label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _update_preview(self) -> None:
        record = self._current_record()
        if record is None:
            return
        original, processed = self._paths_for_record(record)
        self._set_preview_pixmap(self.original_preview, original, "Original unavailable")
        self._set_preview_pixmap(self.processed_preview, processed, "Not processed yet")
        predicted, scale = self.processor.predict_output(record.width, record.height, self._preset())
        preset = self._preset()
        bleed_text = ""
        if preset.fit_mode.casefold() == "mpc bleed (edge extend)":
            bleed_x, bleed_y = self.processor.scaled_bleed(preset)
            trim_width, trim_height = self.processor.trim_dimensions(preset)
            bleed_text = (
                f"<br>MPC bleed: {bleed_x}px L/R, {bleed_y}px T/B"
                f"<br>Trim area: {trim_width} × {trim_height}"
            )
        self.preview_details.setText(
            f"<b>{record.filename}</b><br>{record.relative_path}<br>"
            f"Original: {record.width} × {record.height}<br>"
            f"Predicted: {predicted[0]} × {predicted[1]} ({scale * 100:.1f}%)"
            f"{bleed_text}<br>{self.processor.scale_warning(scale)}"
        )
        self.open_original_button.setEnabled(original.is_file())
        self.open_processed_button.setEnabled(processed.is_file())
        self.open_folder_button.setEnabled(original.parent.is_dir())

    def _open_original(self) -> None:
        record = self._current_record()
        if record:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._paths_for_record(record)[0])))

    def _open_processed(self) -> None:
        record = self._current_record()
        if record:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._paths_for_record(record)[1])))

    def _open_folder(self) -> None:
        record = self._current_record()
        if record:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._paths_for_record(record)[0].parent)))

    def start_selected_processing(self) -> None:
        if self.thread and self.thread.isRunning():
            return
        ids = set(self._selected_ids())
        records = [r for r in self._queue_records if int(r.id) in ids]
        if not records:
            QMessageBox.information(self, "Nothing selected", "Select one or more queue items first.")
            return
        original_root = Path(self.original_root_getter().strip())
        processed_text = self.processed_root_getter().strip()
        if not original_root.is_dir():
            QMessageBox.warning(self, "Original folder missing", str(original_root))
            return
        if not processed_text:
            QMessageBox.warning(self, "Processed folder required", "Choose a processed artwork folder first.")
            return
        self._start_worker(records, original_root, Path(processed_text))

    def _start_worker(self, records, original_root: Path, processed_root: Path) -> None:
        self.thread = QThread(self)
        self.worker = ProcessingWorker(self.processor, records, original_root, processed_root, self._preset())
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._processing_progress)
        self.worker.finished.connect(self._processing_complete)
        self.worker.failed.connect(self._processing_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._processing_thread_finished)
        self.thread.finished.connect(self.thread.deleteLater)
        self.process_selected_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.processing_progress.setVisible(True)
        self.processing_progress.setRange(0, len(records))
        self.processing_progress.setValue(0)
        self.processing_label.setText(f"Queued {len(records):,} file(s)")
        for record in records:
            row = self._row_by_id.get(int(record.id))
            if row is not None:
                self.queue_table.item(row, 2).setText("PENDING")
        self.thread.start()

    def cancel_processing(self) -> None:
        if self.worker:
            self.worker.cancel()
            self.cancel_button.setEnabled(False)
            self.processing_label.setText("Cancelling after the current file…")

    def _processing_progress(self, current: int, total: int, filename: str) -> None:
        self.processing_progress.setRange(0, max(total, 1))
        self.processing_progress.setValue(current - 1)
        self.processing_progress.setFormat(f"{current:,} / {total:,}")
        self.processing_label.setText(f"Processing {Path(filename).name}")
        for row in range(self.queue_table.rowCount()):
            if self.queue_table.item(row, 1).text() == Path(filename).name:
                self.queue_table.item(row, 2).setText("PROCESSING")
                break

    def _processing_complete(self, summary) -> None:
        self.processing_progress.setValue(summary.completed + summary.failed)
        processed_root = Path(self.processed_root_getter().strip())
        try:
            self.service.index_folder(processed_root, library_kind="processed")
        except Exception as exc:
            QMessageBox.warning(self, "Processed index warning", str(exc))
        failed_ids = set()
        for result in summary.results:
            row = self._row_by_id.get(result.original_id)
            if row is not None:
                self.queue_table.item(row, 2).setText(result.status)
            if result.status == "FAILED":
                failed_ids.add(result.original_id)
        self.retry_failed_button.setEnabled(bool(failed_ids))
        self.retry_failed_button.setProperty("failed_ids", list(failed_ids))
        self.processing_label.setText(
            f"Complete: {summary.completed:,} succeeded, {summary.failed:,} failed, {summary.cancelled:,} cancelled"
        )
        self.status_message.emit(self.processing_label.text())
        self.processing_finished.emit()
        self.refresh()
        if summary.failed:
            messages = [r.message for r in summary.results if r.status == "FAILED"]
            QMessageBox.warning(self, "Processing completed with errors", "\n".join(messages[:10]))

    def _retry_failed(self) -> None:
        ids = set(self.retry_failed_button.property("failed_ids") or [])
        records = [r for r in self._queue_records if int(r.id) in ids]
        if records:
            self._start_worker(records, Path(self.original_root_getter().strip()), Path(self.processed_root_getter().strip()))

    def _processing_failed(self, message: str) -> None:
        self.processing_label.setText("Processing failed")
        QMessageBox.critical(self, "Artwork processing failed", message)

    def _processing_thread_finished(self) -> None:
        self.worker = None
        self.thread = None
        self.process_selected_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.processing_progress.setVisible(False)

    def refresh(self) -> None:
        counts = self.service.pipeline_counts()
        for status, label in self.count_labels.items():
            value = int(counts.get(status, 0))
            label.setText(f"{value:,}")
            if status in self.view_buttons:
                self.view_buttons[status].setEnabled(value > 0)
        self._populate_queue()
        self.process_selected_button.setEnabled(bool(self._queue_records))
        self.status_message.emit("Pipeline status and queue refreshed")

    def _save_backend(self) -> None:
        self.backend_setter(self.backend.currentText(), self.executable.text().strip())
        QMessageBox.information(self, "AI backend saved", "AI enhancement backend settings were saved.")
