from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtGui import QPixmap
from app.services.pricing_service import PricingService
from app.services.print_sheet_export_service import PrintLayoutPreset, PrintSheetExportService
from app.services.mpc_export_service import MPCExportOptions, MPCExportService
from app.services.mpc_preflight_service import MPCPreflightService
from PySide6.QtWidgets import *



class MPCExportWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, service, items, processed_root, output_folder, options, project_name):
        super().__init__()
        self.service = service
        self.items = items
        self.processed_root = processed_root
        self.output_folder = output_folder
        self.options = options
        self.project_name = project_name

    def run(self):
        try:
            result = self.service.export(
                self.items,
                self.processed_root,
                self.output_folder,
                self.options,
                self.project_name,
                progress_callback=lambda current, total: self.progress.emit(current, total),
            )
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class PrintProjectBuilder(QWidget):
    status_message = Signal(str)

    def __init__(self, service, processed_root_getter, output_root_getter, pricing_workbook_getter, parent=None):
        super().__init__(parent)
        self.service = service
        self.processed_root_getter = processed_root_getter
        self.output_root_getter = output_root_getter
        self.pricing_workbook_getter = pricing_workbook_getter
        self.pricing_service = PricingService()
        self.export_service = PrintSheetExportService()
        self.mpc_export_service = MPCExportService()
        self.mpc_preflight_service = MPCPreflightService(self.mpc_export_service)
        self.mpc_export_thread = None
        self.mpc_export_worker = None
        self.project_id = None
        self.available = []
        self.project_items = []
        self._build()
        self.refresh_projects()

    def _build(self):
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Print project"))
        self.projects = QComboBox()
        self.projects.currentIndexChanged.connect(self._changed)
        top.addWidget(self.projects, 1)
        for text, fn in [("New",self.new),("Import CSV",self.import_csv),("Rename",self.rename),("Duplicate",self.duplicate),("Delete",self.delete)]:
            button = QPushButton(text); button.clicked.connect(fn); top.addWidget(button)
        layout.addLayout(top)

        back_row = QHBoxLayout()
        back_row.addWidget(QLabel("Project default back:"))
        self.default_back_label = QLabel("Not assigned")
        self.default_back_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        back_row.addWidget(self.default_back_label, 1)
        set_default = QPushButton("Use Selected Artwork")
        set_default.clicked.connect(self.set_default_back)
        clear_default = QPushButton("Clear Default")
        clear_default.clicked.connect(lambda: self._set_default_back(None))
        back_row.addWidget(set_default); back_row.addWidget(clear_default)
        layout.addLayout(back_row)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget(); ll = QVBoxLayout(left)
        search_row = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("Search processed artwork…")
        self.search.textChanged.connect(self.refresh_available)
        add = QPushButton("Add Selected →"); add.clicked.connect(self.add)
        search_row.addWidget(self.search,1); search_row.addWidget(add); ll.addLayout(search_row)
        self.available_table = QTableWidget(0,4)
        self.available_table.setHorizontalHeaderLabels(["Filename","Folder","Resolution","Status"])
        self.available_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.available_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.available_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        ll.addWidget(self.available_table)

        right = QWidget(); rl = QVBoxLayout(right); controls = QHBoxLayout()
        for text, fn in [("Remove",self.remove),("Move Up",lambda:self.move(-1)),("Move Down",lambda:self.move(1)),
                         ("Set Custom Back",self.set_custom_back),("Use Default Back",lambda:self._set_item_back(None))]:
            button=QPushButton(text); button.clicked.connect(fn); controls.addWidget(button)
        export=QPushButton("Export CSV"); export.clicked.connect(self.export); controls.addStretch(); controls.addWidget(export)
        rl.addLayout(controls)

        self.table = QTableWidget(0,7)
        self.table.setHorizontalHeaderLabels(["Artwork","Quantity","Resolution","Back","Back Status","Status","Path"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.preview_selected)
        self.table.setColumnWidth(0,180); self.table.setColumnWidth(3,170); self.table.setColumnWidth(4,100)
        self.table.horizontalHeader().setStretchLastSection(True)
        rl.addWidget(self.table)

        previews = QHBoxLayout()
        front_box=QGroupBox("Front"); front_layout=QVBoxLayout(front_box)
        self.front_preview=QLabel("Select a project item"); self.front_preview.setMinimumHeight(240)
        self.front_preview.setAlignment(Qt.AlignmentFlag.AlignCenter); self.front_preview.setStyleSheet("border:1px solid palette(mid);")
        front_layout.addWidget(self.front_preview)
        back_box=QGroupBox("Effective Back"); back_layout=QVBoxLayout(back_box)
        self.back_preview=QLabel("No back assigned"); self.back_preview.setMinimumHeight(240)
        self.back_preview.setAlignment(Qt.AlignmentFlag.AlignCenter); self.back_preview.setStyleSheet("border:1px solid palette(mid);")
        back_layout.addWidget(self.back_preview)
        previews.addWidget(front_box); previews.addWidget(back_box)
        rl.addLayout(previews)
        self.summary=QLabel(); rl.addWidget(self.summary)
        split.addWidget(left); split.addWidget(right); split.setSizes([480,760]); layout.addWidget(split,1)

        tools = QGroupBox("MPC Price Estimate and Print Export")
        tools_layout = QVBoxLayout(tools)

        price_row = QHBoxLayout()
        self.price_cards_label = QLabel("0 project cards")
        self.price_result_label = QLabel("Load a pricing workbook to estimate MPC cost.")
        self.price_result_label.setWordWrap(True)
        check_price = QPushButton("Check MPC Price")
        check_price.clicked.connect(self.check_price)
        price_row.addWidget(self.price_cards_label)
        price_row.addWidget(self.price_result_label, 1)
        price_row.addWidget(check_price)
        tools_layout.addLayout(price_row)

        export_row = QHBoxLayout()
        self.export_width = QSpinBox(); self.export_width.setRange(100, 10000); self.export_width.setValue(1600)
        self.export_height = QSpinBox(); self.export_height.setRange(100, 10000); self.export_height.setValue(2400)
        self.export_columns = QSpinBox(); self.export_columns.setRange(1, 10); self.export_columns.setValue(3)
        self.export_rows = QSpinBox(); self.export_rows.setRange(1, 10); self.export_rows.setValue(3)
        self.export_gap = QSpinBox(); self.export_gap.setRange(0, 1000); self.export_gap.setValue(0)
        self.export_margin = QSpinBox(); self.export_margin.setRange(0, 2000); self.export_margin.setValue(0)
        self.mirror_backs = QCheckBox("Mirror back columns for duplex")
        self.mirror_backs.setChecked(True)
        for label, widget in [
            ("Card W", self.export_width), ("Card H", self.export_height),
            ("Columns", self.export_columns), ("Rows", self.export_rows),
            ("Gap", self.export_gap), ("Margin", self.export_margin),
        ]:
            export_row.addWidget(QLabel(label))
            export_row.addWidget(widget)
        export_row.addWidget(self.mirror_backs)
        export_png = QPushButton("Export PNG Sheets")
        export_png.clicked.connect(self.export_png_sheets)
        export_pdf = QPushButton("Export Duplex PDF")
        export_pdf.clicked.connect(self.export_pdf_sheets)
        export_row.addWidget(export_png)
        export_row.addWidget(export_pdf)
        tools_layout.addLayout(export_row)
        layout.addWidget(tools)

        mpc_box = QGroupBox("MPC Upload Package")
        mpc_outer = QVBoxLayout(mpc_box)
        mpc_layout = QHBoxLayout()
        mpc_layout.addWidget(QLabel("Deck capacity"))
        self.mpc_capacity_mode = QComboBox()
        self.mpc_capacity_mode.addItems(["Automatic (lowest price)", "Manual"])
        self.mpc_capacity_mode.currentIndexChanged.connect(
            lambda index: self.mpc_capacity.setEnabled(index == 1)
        )
        self.mpc_capacity = QSpinBox()
        self.mpc_capacity.setRange(1, 9999)
        self.mpc_capacity.setValue(612)
        self.mpc_capacity.setEnabled(False)
        self.mpc_split_custom = QCheckBox("Split standard and custom backs")
        self.mpc_split_custom.setChecked(True)
        self.mpc_hardlinks = QCheckBox("Use hard links when possible")
        self.mpc_hardlinks.setToolTip(
            "Saves disk space when the output is on the same drive. "
            "Falls back to normal copies when hard links are unavailable."
        )
        preflight_mpc = QPushButton("Run MPC Preflight")
        preflight_mpc.clicked.connect(self.run_mpc_preflight)
        preview_mpc = QPushButton("Preview MPC Split")
        preview_mpc.clicked.connect(self.preview_mpc_export)
        self.export_mpc_button = QPushButton("Export MPC Upload Package")
        self.export_mpc_button.clicked.connect(self.export_mpc_package)
        mpc_layout.addWidget(self.mpc_capacity_mode)
        mpc_layout.addWidget(self.mpc_capacity)
        mpc_layout.addWidget(self.mpc_split_custom)
        mpc_layout.addWidget(self.mpc_hardlinks)
        mpc_layout.addStretch(1)
        mpc_layout.addWidget(preflight_mpc)
        mpc_layout.addWidget(preview_mpc)
        mpc_layout.addWidget(self.export_mpc_button)
        mpc_outer.addLayout(mpc_layout)

        self.mpc_progress = QProgressBar()
        self.mpc_progress.setVisible(False)
        self.mpc_progress_status = QLabel()
        self.mpc_progress_status.setVisible(False)
        self.mpc_progress_status.setWordWrap(True)
        mpc_outer.addWidget(self.mpc_progress)
        mpc_outer.addWidget(self.mpc_progress_status)
        layout.addWidget(mpc_box)

    def selected_available_id(self):
        rows=self.available_table.selectionModel().selectedRows()
        if len(rows)!=1: return None
        return int(self.available_table.item(rows[0].row(),0).data(Qt.ItemDataRole.UserRole))

    def refresh_projects(self, select=None):
        rows=self.service.list_projects(); self.projects.blockSignals(True); self.projects.clear()
        for row in rows: self.projects.addItem(f"{row['name']} ({row['total_cards']} cards)",row['id'])
        self.projects.blockSignals(False)
        if rows:
            index=next((i for i in range(self.projects.count()) if self.projects.itemData(i)==select),0)
            self.projects.setCurrentIndex(index); self._changed(index)
        else:
            self.project_id=None; self.default_back_label.setText("Not assigned"); self.refresh_available(); self.refresh_items()

    def _changed(self,index):
        self.project_id=self.projects.itemData(index) if index>=0 else None
        project=self.service.project(self.project_id) if self.project_id else None
        self.default_back_label.setText(project.get("default_back_relative_path") or "Not assigned" if project else "Not assigned")
        self.refresh_available(); self.refresh_items()

    def set_default_back(self):
        artwork_id=self.selected_available_id()
        if artwork_id is None:
            QMessageBox.information(self,"Select one artwork","Select one processed artwork to use as the default back."); return
        self._set_default_back(artwork_id)

    def _set_default_back(self, artwork_id):
        if not self.project_id: return
        self.service.set_default_back(self.project_id,artwork_id)
        self.refresh_projects(self.project_id)

    def set_custom_back(self):
        artwork_id=self.selected_available_id()
        if artwork_id is None:
            QMessageBox.information(self,"Select one artwork","Select one processed artwork to use as the custom back."); return
        self._set_item_back(artwork_id)

    def _set_item_back(self, artwork_id):
        row=self.selected()
        if not row:
            QMessageBox.information(self,"Select a project card","Select a project card first."); return
        self.service.set_item_back(row["id"],artwork_id)
        self.refresh_items()

    def import_csv(self):
        path,_=QFileDialog.getOpenFileName(self,"Import print project from CSV",str(Path(self.output_root_getter() or ".")),"CSV files (*.csv)")
        if not path:return
        try: analysis=self.service.analyze_csv(Path(path))
        except Exception as exc: QMessageBox.warning(self,"Could not read CSV",str(exc)); return
        dialog=QDialog(self); dialog.setWindowTitle("Import Print Project from CSV"); dialog.resize(1050,650); layout=QVBoxLayout(dialog)
        name_row=QHBoxLayout(); name_row.addWidget(QLabel("Project name")); name_edit=QLineEdit(analysis["suggested_name"]); name_row.addWidget(name_edit,1); layout.addLayout(name_row)
        summary=QLabel(f"{analysis['matched']:,} fronts matched • {analysis['custom_backs']:,} custom backs matched • {analysis['back_warnings']:,} back warning(s) • {analysis['unmatched']+analysis['ambiguous']+analysis['invalid']:,} front row(s) skipped")
        summary.setWordWrap(True); layout.addWidget(summary)
        table=QTableWidget(len(analysis["rows"]),8)
        table.setHorizontalHeaderLabels(["CSV Row","Front","Front Path","Qty","Front Status","Back","Back Status","Details"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers); table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows); table.setAlternatingRowColors(True); table.verticalHeader().setVisible(False)
        widths=[65,160,220,55,90,160,100]
        for i,w in enumerate(widths):table.setColumnWidth(i,w)
        for row_number,row in enumerate(analysis["rows"]):
            values=[row["source_row"],row.get("matched_filename") or row.get("filename","") ,row.get("matched_relative_path") or row.get("relative_path","") ,row.get("quantity","") ,row["status"],row.get("matched_back_filename") or row.get("back_filename","") ,row.get("back_status","") ,f"{row.get('message','')} {row.get('back_message','')}".strip()]
            for column,value in enumerate(values):table.setItem(row_number,column,QTableWidgetItem(str(value)))
        table.horizontalHeader().setStretchLastSection(True); layout.addWidget(table,1)
        note=QLabel("Only matched front rows are imported. A valid custom back is saved per card; otherwise the card uses the project's default back after import."); note.setWordWrap(True); layout.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel|QDialogButtonBox.StandardButton.Ok); ok=buttons.button(QDialogButtonBox.StandardButton.Ok); ok.setText("Create Project"); ok.setEnabled(analysis["matched"]>0)
        def create():
            name=name_edit.text().strip()
            if not name: QMessageBox.warning(dialog,"Project name required","Enter a project name."); return
            try: project_id=self.service.create_project_from_csv(analysis,name)
            except Exception as exc: QMessageBox.warning(dialog,"Could not import CSV",str(exc)); return
            dialog.accept(); self.refresh_projects(project_id); self.status_message.emit(f"Imported print project '{name}' with {analysis['matched']:,} matched row(s).")
        buttons.accepted.connect(create); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons); dialog.exec()

    def new(self):
        name,ok=QInputDialog.getText(self,"New print project","Project name:")
        if ok:
            try:self.refresh_projects(self.service.create_project(name))
            except Exception as exc:QMessageBox.warning(self,"Could not create project",str(exc))
    def rename(self):
        if not self.project_id:return
        name,ok=QInputDialog.getText(self,"Rename print project","Project name:",text=self.projects.currentText().rsplit(" (",1)[0])
        if ok:
            try:self.service.rename_project(self.project_id,name);self.refresh_projects(self.project_id)
            except Exception as exc:QMessageBox.warning(self,"Could not rename project",str(exc))
    def duplicate(self):
        if not self.project_id:return
        name,ok=QInputDialog.getText(self,"Duplicate print project","New project name:",text=self.projects.currentText().rsplit(" (",1)[0]+" Copy")
        if ok:
            try:self.refresh_projects(self.service.duplicate_project(self.project_id,name))
            except Exception as exc:QMessageBox.warning(self,"Could not duplicate project",str(exc))
    def delete(self):
        if self.project_id and QMessageBox.question(self,"Delete project","Delete this print project and its item list?")==QMessageBox.StandardButton.Yes:
            self.service.delete_project(self.project_id);self.refresh_projects()

    def refresh_available(self):
        self.available=self.service.available_artwork(self.search.text());self.available_table.setRowCount(len(self.available))
        for row,record in enumerate(self.available):
            folder=str(Path(record["relative_path"]).parent); values=[record["filename"],"" if folder=="." else folder,f"{record['width']} × {record['height']}",record["status"]]
            for column,value in enumerate(values):
                item=QTableWidgetItem(str(value));item.setData(Qt.ItemDataRole.UserRole,record["id"]);self.available_table.setItem(row,column,item)

    def add(self):
        if not self.project_id: QMessageBox.information(self,"Print project required","Create or select a print project first.");return
        ids=[int(self.available_table.item(x.row(),0).data(Qt.ItemDataRole.UserRole)) for x in self.available_table.selectionModel().selectedRows()]
        if ids:self.service.add_artwork(self.project_id,ids);self.refresh_items();self.refresh_projects(self.project_id)

    def refresh_items(self):
        self.project_items=self.service.items(self.project_id) if self.project_id else [];self.table.setRowCount(len(self.project_items));total=0;missing=0
        for row,record in enumerate(self.project_items):
            total+=record["quantity"];missing+=record["back_status"]=="Missing Back"
            item=QTableWidgetItem(record["filename"]);item.setData(Qt.ItemDataRole.UserRole,record["id"]);self.table.setItem(row,0,item)
            spin=QSpinBox();spin.setRange(1,999);spin.setValue(record["quantity"]);spin.valueChanged.connect(lambda value,item_id=record["id"]:self.setq(item_id,value));self.table.setCellWidget(row,1,spin)
            values={2:f"{record['width']} × {record['height']}",3:record["effective_back_filename"] or "—",4:record["back_status"],5:record["status"],6:record["relative_path"]}
            for column,value in values.items():self.table.setItem(row,column,QTableWidgetItem(str(value)))
        warning=f" • {missing:,} missing back(s)" if missing else ""
        self.summary.setText(f"{len(self.project_items):,} unique card(s) • {total:,} total copies{warning}")
        self.price_cards_label.setText(f"{total:,} project card(s)")

    def setq(self,item_id,value):self.service.set_quantity(item_id,value);self.refresh_projects(self.project_id)
    def selected(self):
        rows=self.table.selectionModel().selectedRows();return self.project_items[rows[0].row()] if rows else None
    def remove(self):
        row=self.selected()
        if row:self.service.remove_item(row["id"]);self.refresh_items();self.refresh_projects(self.project_id)
    def move(self,direction):
        row=self.selected()
        if row:self.service.move_item(self.project_id,row["id"],direction);self.refresh_items()

    def _show_pixmap(self,label,path,empty_text):
        pix=QPixmap(str(path)) if path else QPixmap()
        if pix.isNull():label.setText(empty_text);label.setPixmap(QPixmap())
        else:label.setText("");label.setPixmap(pix.scaled(label.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))

    def preview_selected(self):
        row=self.selected()
        if not row:return
        root=Path(self.processed_root_getter())
        self._show_pixmap(self.front_preview,root/row["relative_path"],"Front preview unavailable")
        back_path=root/row["effective_back_relative_path"] if row["effective_back_relative_path"] else None
        self._show_pixmap(self.back_preview,back_path,"No back assigned")

    def _project_name(self):
        return self.projects.currentText().rsplit(" (", 1)[0] if self.project_id else "Print_Project"

    def _layout_preset(self):
        return PrintLayoutPreset(
            card_width_px=self.export_width.value(),
            card_height_px=self.export_height.value(),
            columns=self.export_columns.value(),
            rows=self.export_rows.value(),
            gap_px=self.export_gap.value(),
            margin_px=self.export_margin.value(),
            mirror_backs_horizontally=self.mirror_backs.isChecked(),
        )

    def check_price(self):
        total_cards = sum(int(item["quantity"]) for item in self.project_items)
        if total_cards < 1:
            QMessageBox.information(self, "Empty project", "Add cards to the project first.")
            return
        pricing_path = Path(self.pricing_workbook_getter() or "")
        if not pricing_path.is_file():
            QMessageBox.warning(
                self,
                "Pricing workbook missing",
                "Select the MPC pricing workbook in the Project tab first.",
            )
            return
        try:
            summary = self.pricing_service.load(pricing_path)
            options = self.pricing_service.quote_options(summary, total_cards)
        except Exception as exc:
            QMessageBox.warning(self, "Could not calculate MPC price", str(exc))
            return
        if not options:
            QMessageBox.warning(
                self,
                "No price available",
                "No usable pricing option was found for this project.",
            )
            return

        best = options[0]
        self.price_result_label.setText(
            f"Recommended: {best['deck_count']:,} deck(s) × "
            f"{best['deck_capacity']:,} cards at ${best['unit_price']:,.2f} each "
            f"({best['quantity_tier']}) = ${best['total_price']:,.2f}; "
            f"{best['unused_slots']:,} unused slot(s)."
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("MPC Price Options")
        dialog.resize(720, 480)
        layout = QVBoxLayout(dialog)
        note = QLabel(
            "Estimates use the inserted workbook. Each option packs the entire "
            "project into decks of one capacity and applies the workbook's "
            "number-of-decks tier. Shipping, tax, and optional MPC upgrades are "
            "not included."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        table = QTableWidget(len(options), 6)
        table.setHorizontalHeaderLabels(
            ["Deck Size", "Decks", "Tier", "Price / Deck", "Total", "Unused Slots"]
        )
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for row, option in enumerate(options):
            values = [
                option["deck_capacity"],
                option["deck_count"],
                option["quantity_tier"],
                f"${option['unit_price']:,.2f}",
                f"${option['total_price']:,.2f}",
                option["unused_slots"],
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(str(value)))
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table, 1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec()

    def _validate_export(self):
        if not self.project_id or not self.project_items:
            QMessageBox.information(self, "Empty project", "Add cards to the project first.")
            return False
        missing = [item["filename"] for item in self.project_items if not item.get("effective_back_relative_path")]
        if missing:
            QMessageBox.warning(
                self,
                "Missing card backs",
                f"{len(missing):,} project item(s) have no effective back. "
                "Assign a project default back or custom backs before exporting.",
            )
            return False
        return True

    def export_png_sheets(self):
        if not self._validate_export():
            return
        default = Path(self.output_root_getter() or ".") / (self._project_name() + "_Sheets")
        folder = QFileDialog.getExistingDirectory(
            self, "Choose PNG sheet output folder", str(default)
        )
        if not folder:
            return
        try:
            result = self.export_service.export_png_sheets(
                self.project_items,
                Path(self.processed_root_getter()),
                Path(folder),
                self._layout_preset(),
                self._project_name(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Print export failed", str(exc))
            return
        QMessageBox.information(
            self,
            "Print sheets exported",
            f"{result['total_cards']:,} cards exported across "
            f"{result['sheet_count']:,} front/back sheet pair(s).\n"
            f"Unused slots: {result['unfilled_slots']:,}\n\n{folder}",
        )

    def export_pdf_sheets(self):
        if not self._validate_export():
            return
        default = Path(self.output_root_getter() or ".") / (self._project_name() + "_Duplex.pdf")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export duplex PDF", str(default), "PDF files (*.pdf)"
        )
        if not path:
            return
        try:
            result = self.export_service.export_pdf(
                self.project_items,
                Path(self.processed_root_getter()),
                Path(path),
                self._layout_preset(),
                self._project_name(),
            )
        except Exception as exc:
            QMessageBox.critical(self, "PDF export failed", str(exc))
            return
        QMessageBox.information(
            self,
            "Duplex PDF exported",
            f"{result['total_cards']:,} cards exported to "
            f"{result['pdf_pages']:,} PDF page(s).\n"
            f"Unused slots: {result['unfilled_slots']:,}\n\n{path}",
        )

    def _mpc_deck_capacity(self):
        if self.mpc_capacity_mode.currentIndex() == 1:
            return self.mpc_capacity.value()

        total_cards = sum(int(item["quantity"]) for item in self.project_items)
        pricing_path = Path(self.pricing_workbook_getter() or "")
        if not pricing_path.is_file():
            raise ValueError(
                "Automatic deck capacity requires the MPC pricing workbook. "
                "Select it in the Project tab or choose Manual capacity."
            )
        summary = self.pricing_service.load(pricing_path)
        best = self.pricing_service.best_quote(summary, total_cards)
        return int(best["deck_capacity"])

    def _mpc_options(self):
        return MPCExportOptions(
            deck_capacity=self._mpc_deck_capacity(),
            split_custom_backs=self.mpc_split_custom.isChecked(),
            use_hardlinks=self.mpc_hardlinks.isChecked(),
        )

    def _run_mpc_preflight(self):
        options = self._mpc_options()
        return self.mpc_preflight_service.check(
            self.project_items,
            Path(self.processed_root_getter()),
            options,
            minimum_width=self.export_width.value(),
            minimum_height=self.export_height.value(),
        )

    def run_mpc_preflight(self):
        if not self.project_id:
            QMessageBox.information(
                self,
                "Print project required",
                "Create or select a print project first.",
            )
            return
        try:
            result = self._run_mpc_preflight()
        except Exception as exc:
            QMessageBox.warning(self, "MPC preflight failed", str(exc))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("MPC Preflight")
        dialog.resize(940, 620)
        layout = QVBoxLayout(dialog)

        state = "READY TO EXPORT" if result["passed"] else "NOT READY"
        summary = QLabel(
            f"<b>{state}</b> • {result['total_cards']:,} cards • "
            f"{result['unique_files_checked']:,} unique files checked • "
            f"{result['errors']:,} error(s) • "
            f"{result['warnings']:,} warning(s)"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        if result["plan"]:
            plan = result["plan"]
            deck_summary = QLabel(
                f"{plan['total_decks']:,} deck(s) planned at capacity "
                f"{plan['deck_capacity']:,}; "
                f"{plan['unused_slots']:,} unused slot(s). "
                f"Standard-back remainder moved to individually backed decks: "
                f"{plan['transferred_standard_cards']:,}."
            )
            deck_summary.setWordWrap(True)
            layout.addWidget(deck_summary)

        issues = result["issues"]
        table = QTableWidget(len(issues), 4)
        table.setHorizontalHeaderLabels(
            ["Severity", "Category", "Artwork", "Details"]
        )
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.setColumnWidth(0, 85)
        table.setColumnWidth(1, 130)
        table.setColumnWidth(2, 220)
        for row, issue in enumerate(issues):
            values = [
                issue.severity,
                issue.category,
                issue.filename,
                issue.message,
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(str(value)))
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table, 1)

        if not issues:
            table.setRowCount(1)
            table.setItem(0, 0, QTableWidgetItem("Pass"))
            table.setItem(0, 3, QTableWidgetItem(
                "No preflight problems were found."
            ))

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec()
        return result

    def preview_mpc_export(self):
        if not self._validate_export():
            return
        try:
            options = self._mpc_options()
            plan = self.mpc_export_service.plan(
                self.project_items,
                Path(self.processed_root_getter()),
                options,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Could not preview MPC export", str(exc))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("MPC Upload Package Preview")
        dialog.resize(680, 460)
        layout = QVBoxLayout(dialog)
        summary = QLabel(
            f"{plan['total_cards']:,} cards • {plan['total_decks']:,} deck(s) • "
            f"capacity {plan['deck_capacity']:,} • {plan['unused_slots']:,} unused slot(s)"
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        table = QTableWidget(len(plan["groups"]), 5)
        table.setHorizontalHeaderLabels(
            ["Export Group", "Cards", "Decks", "Deck Sizes", "Unused Slots"]
        )
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, group in enumerate(plan["groups"]):
            values = [
                group["name"].replace("_", " "),
                group["card_count"],
                group["deck_count"],
                ", ".join(str(value) for value in group["deck_sizes"]),
                group["unused_slots"],
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(str(value)))
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table, 1)

        note = QLabel(
            "Standard-back decks contain ordered fronts plus one common back file. "
            "Custom-back decks contain ordered Fronts and Backs folders with matching numeric prefixes."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dialog.reject)
        layout.addWidget(close)
        dialog.exec()

    def export_mpc_package(self):
        if self.mpc_export_thread and self.mpc_export_thread.isRunning():
            return
        if not self._validate_export():
            return
        try:
            options = self._mpc_options()
            preflight = self.mpc_preflight_service.check(
                self.project_items,
                Path(self.processed_root_getter()),
                options,
                minimum_width=self.export_width.value(),
                minimum_height=self.export_height.value(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "MPC export settings", str(exc))
            return

        if not preflight["passed"]:
            QMessageBox.warning(
                self,
                "MPC preflight errors",
                f"Export is blocked by {preflight['errors']:,} preflight "
                "error(s). Run MPC Preflight to review the problems.",
            )
            return

        if preflight["warnings"]:
            answer = QMessageBox.question(
                self,
                "MPC preflight warnings",
                f"Preflight found {preflight['warnings']:,} warning(s), but "
                "no blocking errors. Continue with the export?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        default = Path(self.output_root_getter() or ".") / (
            self._project_name() + "_MPC_Export"
        )
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose MPC export folder",
            str(default),
        )
        if not folder:
            return

        output_folder = Path(folder) / self._project_name()
        if output_folder.exists() and any(output_folder.iterdir()):
            answer = QMessageBox.question(
                self,
                "Export folder is not empty",
                f"The folder already contains files:\n\n{output_folder}\n\n"
                "Continue and overwrite files with matching names?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.export_mpc_button.setEnabled(False)
        self.mpc_progress.setVisible(True)
        self.mpc_progress_status.setVisible(True)
        self.mpc_progress.setRange(0, 0)
        self.mpc_progress_status.setText("Preparing MPC upload package…")
        self.status_message.emit("Exporting MPC upload package…")

        self.mpc_export_thread = QThread(self)
        self.mpc_export_worker = MPCExportWorker(
            self.mpc_export_service,
            list(self.project_items),
            Path(self.processed_root_getter()),
            output_folder,
            options,
            self._project_name(),
        )
        self.mpc_export_worker.moveToThread(self.mpc_export_thread)
        self.mpc_export_thread.started.connect(self.mpc_export_worker.run)
        self.mpc_export_worker.progress.connect(self._mpc_export_progress)
        self.mpc_export_worker.finished.connect(self._mpc_export_finished)
        self.mpc_export_worker.failed.connect(self._mpc_export_failed)
        self.mpc_export_worker.finished.connect(self.mpc_export_thread.quit)
        self.mpc_export_worker.failed.connect(self.mpc_export_thread.quit)
        self.mpc_export_thread.finished.connect(self.mpc_export_worker.deleteLater)
        self.mpc_export_thread.finished.connect(self._mpc_export_thread_finished)
        self.mpc_export_thread.finished.connect(self.mpc_export_thread.deleteLater)
        self.mpc_export_thread.start()

    def _mpc_export_progress(self, current, total):
        self.mpc_progress.setRange(0, max(total, 1))
        self.mpc_progress.setValue(current)
        self.mpc_progress.setFormat(f"Deck {current:,} of {total:,} — %p%")
        self.mpc_progress_status.setText(
            f"Finished deck {current:,} of {total:,}."
        )

    def _mpc_export_finished(self, result):
        self.export_mpc_button.setEnabled(True)
        self.mpc_progress.setRange(0, max(result["total_decks"], 1))
        self.mpc_progress.setValue(result["total_decks"])
        self.mpc_progress.setFormat("MPC export complete — 100%")
        self.mpc_progress_status.setText(
            f"Finished exporting {result['total_cards']:,} cards into "
            f"{result['total_decks']:,} deck folder(s)."
        )
        self.status_message.emit("MPC upload package export complete")
        QMessageBox.information(
            self,
            "MPC upload package exported",
            f"{result['total_cards']:,} cards exported into "
            f"{result['total_decks']:,} deck folder(s).\n"
            f"Standard-back cards: {result['standard_cards']:,}\n"
            f"Cards in individually backed decks: {result['custom_cards']:,}\n"
            f"Unused slots: {result['unused_slots']:,}\n\n"
            f"{result['output_folder']}",
        )

    def _mpc_export_failed(self, message):
        self.export_mpc_button.setEnabled(True)
        self.mpc_progress.setRange(0, 1)
        self.mpc_progress.setValue(0)
        self.mpc_progress.setFormat("Export failed")
        self.mpc_progress_status.setText(message)
        self.status_message.emit("MPC upload package export failed")
        QMessageBox.critical(self, "MPC export failed", message)

    def _mpc_export_thread_finished(self):
        self.mpc_export_worker = None
        self.mpc_export_thread = None


    def export(self):
        if not self.project_id:return
        default=Path(self.output_root_getter() or ".")/(self.projects.currentText().rsplit(" (",1)[0]+".csv")
        path,_=QFileDialog.getSaveFileName(self,"Export project manifest",str(default),"CSV files (*.csv)")
        if path:self.service.export_csv(self.project_id,Path(path));self.status_message.emit(f"Exported print project manifest: {path}")
