"""Ingest view — replace ``synapse ingest`` with a GUI workflow."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from synapse.gui.bridge import SynapseBridge
from synapse.gui.widgets.file_picker import FilePicker
from synapse.gui.widgets.safe_log import SafeViewLogHandler
from synapse.gui.workers import AsyncWorker

logger = logging.getLogger(__name__)


class IngestView(QWidget):
    """GUI for ingesting documents into the knowledge graph."""

    def __init__(self, bridge: SynapseBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._worker: AsyncWorker | None = None
        self._active_workers: set = set()

        # -- Header -----------------------------------------------------------
        header = QLabel("Ingest Documents")
        header.setFont(QFont("", 24, QFont.Weight.Bold))

        description = QLabel(
            "Select documents to extract entities and relationships "
            "into the knowledge graph (PDF, Markdown, text, HTML, email). Requires bootstrap (init) first."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #8e8ea0; font-size: 14px; margin-bottom: 8px;")

        # -- File picker ------------------------------------------------------
        self._file_picker = FilePicker()

        # -- Options ----------------------------------------------------------
        self._reset_check = QCheckBox("Reset graph before ingestion")
        self._dry_run_check = QCheckBox("Dry run (extract but don't store)")

        opts_row = QHBoxLayout()
        opts_row.addWidget(self._reset_check)
        opts_row.addWidget(self._dry_run_check)
        opts_row.addStretch()

        # -- Start button + progress ------------------------------------------
        self._start_btn = QPushButton("Start Ingestion")
        self._start_btn.clicked.connect(self._on_start)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # indeterminate
        self._progress_bar.setVisible(False)
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setTextVisible(False)

        # -- Log area ---------------------------------------------------------
        self._log_area = QPlainTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setFont(QFont("Menlo", 11))
        self._log_area.setMaximumHeight(200)
        self._log_area.setPlaceholderText("Logs will appear here...")

        # -- Summary (hidden until complete) ----------------------------------
        self._summary_box = QGroupBox("Ingestion Complete")
        self._summary_box.setVisible(False)
        summary_layout = QVBoxLayout(self._summary_box)

        self._summary_table = QTableWidget(0, 2)
        self._summary_table.setHorizontalHeaderLabels(["Metric", "Value"])
        self._summary_table.horizontalHeader().setStretchLastSection(True)
        self._summary_table.setMaximumHeight(200)
        summary_layout.addWidget(self._summary_table)

        # -- Layout -----------------------------------------------------------
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(header)
        layout.addWidget(description)
        layout.addWidget(self._file_picker)
        layout.addLayout(opts_row)
        layout.addWidget(self._start_btn)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._log_area)
        layout.addWidget(self._summary_box)
        layout.addStretch()

        # -- Log capture (thread-safe via signal) -----------------------------
        self._log_handler = SafeViewLogHandler()
        self._log_handler.bridge.log_received.connect(self._append_log)
        logging.getLogger("synapse.extraction").addHandler(self._log_handler)

    # -- Actions --------------------------------------------------------------

    @Slot()
    def _on_start(self) -> None:
        files = self._file_picker.files
        if not files:
            QMessageBox.warning(self, "No files", "Please select at least one document.")
            return

        settings = self._bridge.settings
        if not settings.llm_api_key:
            QMessageBox.critical(self, "Missing API Key", "SYNAPSE_LLM_API_KEY is not set.")
            return

        store = self._bridge.get_store()
        if not store.is_bootstrapped():
            QMessageBox.warning(
                self, "Not bootstrapped",
                "This instance has not been bootstrapped yet.\n"
                "Please run Init first."
            )
            return

        reset = self._reset_check.isChecked()
        dry_run = self._dry_run_check.isChecked()

        self._start_btn.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._summary_box.setVisible(False)
        self._log_area.clear()

        def make_coro():
            from synapse.extraction.pipeline import ingest_files
            return ingest_files(files, settings, reset=reset, dry_run=dry_run)

        worker = AsyncWorker(make_coro)
        self._worker = worker
        self._active_workers.add(worker)
        worker.signals.finished.connect(self._on_finished)
        worker.signals.error.connect(self._on_error)
        worker.start()

    @Slot(object)
    def _on_finished(self, result: dict) -> None:
        self._start_btn.setEnabled(True)
        self._progress_bar.setVisible(False)
        self._active_workers.discard(self._worker)
        self._worker = None

        self._summary_box.setVisible(True)
        rows = [
            ("Documents processed", str(result.get("documents", 0))),
            ("Entities extracted", str(result.get("total_entities", 0))),
            ("Relationships extracted", str(result.get("total_relationships", 0))),
        ]
        if result.get("graph_nodes") is not None:
            rows.append(("Graph nodes", str(result["graph_nodes"])))
            rows.append(("Graph edges", str(result["graph_edges"])))

        entity_counts = result.get("entity_counts", {})
        for etype, count in entity_counts.items():
            rows.append((f"  {etype}", str(count)))

        errors = result.get("errors", [])
        if errors:
            rows.append(("Errors", str(len(errors))))
            for err in errors[:5]:
                rows.append((f"  {err.get('file', '?')}", str(err.get("error", "?"))))

        self._summary_table.setRowCount(len(rows))
        for i, (metric, value) in enumerate(rows):
            self._summary_table.setItem(i, 0, QTableWidgetItem(metric))
            self._summary_table.setItem(i, 1, QTableWidgetItem(value))

        logger.info("Ingestion completed successfully")

    @Slot(str)
    def _on_error(self, traceback_str: str) -> None:
        self._start_btn.setEnabled(True)
        self._progress_bar.setVisible(False)
        self._active_workers.discard(self._worker)
        self._worker = None
        self._append_log("ERROR", f"Ingestion failed:\n{traceback_str}")
        QMessageBox.critical(self, "Ingestion Failed", f"Error:\n\n{traceback_str}")

    def _append_log(self, level: str, message: str) -> None:
        self._log_area.appendPlainText(f"[{level}] {message}")


