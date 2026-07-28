from __future__ import annotations
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QGroupBox,QFormLayout,QLabel,QComboBox,QLineEdit,QPushButton,QMessageBox
from app.services.artwork_index_service import ArtworkIndexService

class PipelineDashboard(QWidget):
    status_message=Signal(str)
    def __init__(self,service:ArtworkIndexService,backend_getter,backend_setter,parent=None):
        super().__init__(parent); self.service=service; self.backend_getter=backend_getter; self.backend_setter=backend_setter; self._build(); self.refresh()
    def _build(self):
        layout=QVBoxLayout(self)
        status=QGroupBox('Artwork Pipeline Status'); form=QFormLayout(status)
        self.total=QLabel('0'); self.ready=QLabel('0'); self.needs=QLabel('0'); self.stale=QLabel('0')
        form.addRow('Originals indexed',self.total); form.addRow('Ready processed outputs',self.ready); form.addRow('Needs processing',self.needs); form.addRow('Needs rebuild',self.stale)
        layout.addWidget(status)
        ai=QGroupBox('AI Enhancement Backend'); aiform=QFormLayout(ai)
        self.backend=QComboBox(); self.backend.addItems(['None','External executable','Real-ESRGAN (external)','SwinIR (external)'])
        self.executable=QLineEdit(); self.executable.setPlaceholderText('Path to AI enhancer executable or script')
        current,path=self.backend_getter(); self.backend.setCurrentText(current); self.executable.setText(path)
        save=QPushButton('Save AI Backend Settings'); save.clicked.connect(self._save)
        aiform.addRow('Backend',self.backend); aiform.addRow('Executable',self.executable); aiform.addRow('',save)
        layout.addWidget(ai)
        note=QLabel('LegendForge keeps original artwork as the source of truth. AI models are not bundled. This module records the configured external backend and identifies which originals require processing or rebuilding. Actual batch execution will be added in the next pipeline milestone.')
        note.setWordWrap(True); layout.addWidget(note); layout.addStretch(1)
        row=QHBoxLayout(); refresh=QPushButton('Refresh Pipeline Status'); refresh.clicked.connect(self.refresh); row.addWidget(refresh); row.addStretch(1); layout.addLayout(row)
    def refresh(self):
        c=self.service.pipeline_counts(); self.total.setText(f"{c['TOTAL']:,}"); self.ready.setText(f"{c['READY']:,}"); self.needs.setText(f"{c['NEEDS_PROCESSING']:,}"); self.stale.setText(f"{c['NEEDS_REBUILD']:,}")
        self.status_message.emit('Pipeline status refreshed')
    def _save(self):
        self.backend_setter(self.backend.currentText(),self.executable.text().strip()); QMessageBox.information(self,'AI backend saved','AI enhancement backend settings were saved to the current project.')
