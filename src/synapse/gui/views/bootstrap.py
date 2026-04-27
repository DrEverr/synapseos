"""Bootstrap view — replace ``synapse init`` with a GUI workflow."""

from __future__ import annotations

import logging

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from synapse.gui.bridge import SynapseBridge
from synapse.gui.widgets.file_picker import FilePicker
from synapse.gui.widgets.progress import StepProgress
from synapse.gui.widgets.safe_log import SafeViewLogHandler
from synapse.gui.workers import AsyncWorker

logger = logging.getLogger(__name__)

BOOTSTRAP_STEPS = [
    "Analyze Domain",
    "Discover Ontology",
    "Refine Ontology",
    "Generate Prompts",
]


class BootstrapView(QWidget):
    """GUI for bootstrapping a new domain."""

    bootstrap_completed = Signal(str)  # emits new graph name

    def __init__(self, bridge: SynapseBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._worker: AsyncWorker | None = None
        self._active_workers: set = set()

        # -- Header -----------------------------------------------------------
        header = QLabel("Init / Bootstrap")
        header.setFont(QFont("", 24, QFont.Weight.Bold))

        description = QLabel(
            "Select documents to analyze (PDF, Markdown, text, HTML, email). "
            "SynapseOS will discover the domain, build an ontology, and generate extraction prompts."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #8e8ea0; font-size: 14px; margin-bottom: 8px;")

        # -- Graph name -------------------------------------------------------
        graph_row = QHBoxLayout()
        graph_label = QLabel("New graph name:")
        graph_label.setStyleSheet("font-size: 13px;")
        self._graph_name_input = QLineEdit()
        self._graph_name_input.setPlaceholderText("e.g. cooking, electronics, legal...")
        graph_row.addWidget(graph_label)
        graph_row.addWidget(self._graph_name_input, stretch=1)

        # -- File picker ------------------------------------------------------
        self._file_picker = FilePicker()

        # -- Step progress ----------------------------------------------------
        self._progress = StepProgress(BOOTSTRAP_STEPS)

        # -- Start button -----------------------------------------------------
        self._start_btn = QPushButton("Start Bootstrap")
        self._start_btn.clicked.connect(self._on_start)

        # -- Log area ---------------------------------------------------------
        self._log_area = QPlainTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setFont(QFont("Menlo", 11))
        self._log_area.setMaximumHeight(200)
        self._log_area.setPlaceholderText("Logs will appear here...")

        # -- Summary (hidden until complete) ----------------------------------
        self._summary_box = QGroupBox("Bootstrap Complete")
        self._summary_box.setVisible(False)
        summary_layout = QVBoxLayout(self._summary_box)
        self._summary_label = QLabel()
        self._summary_label.setWordWrap(True)
        self._summary_label.setStyleSheet("font-size: 13px;")
        summary_layout.addWidget(self._summary_label)

        # -- Layout -----------------------------------------------------------
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(header)
        layout.addWidget(description)
        layout.addLayout(graph_row)
        layout.addWidget(self._file_picker)
        layout.addWidget(self._progress)
        layout.addWidget(self._start_btn)
        layout.addWidget(self._log_area)
        layout.addWidget(self._summary_box)
        layout.addStretch()

        # -- Log capture for this view (thread-safe via signal) ----------------
        self._log_handler = SafeViewLogHandler()
        self._log_handler.bridge.log_received.connect(self._append_log)
        logging.getLogger("synapse.bootstrap").addHandler(self._log_handler)

    # -- Actions --------------------------------------------------------------

    @Slot()
    def _on_start(self) -> None:
        graph_name = self._graph_name_input.text().strip()
        if not graph_name:
            QMessageBox.warning(self, "No graph name", "Please enter a name for the new graph.")
            return

        files = self._file_picker.files
        if not files:
            QMessageBox.warning(self, "No files", "Please select at least one document.")
            return

        settings = self._bridge.settings
        if not settings.llm_api_key:
            QMessageBox.critical(self, "Missing API Key", "SYNAPSE_LLM_API_KEY is not set.")
            return

        # Switch to the new graph
        self._bridge.switch_graph(graph_name)

        self._start_btn.setEnabled(False)
        self._summary_box.setVisible(False)
        self._log_area.clear()
        self._progress.reset()
        self._progress.set_step(0)

        def make_coro():
            from synapse.bootstrap.pipeline import bootstrap
            from synapse.storage.instance_store import InstanceStore
            store = InstanceStore(settings.get_instance_dir() / "instance.db")
            return bootstrap(files, settings, store)

        worker = AsyncWorker(make_coro)
        self._worker = worker
        self._active_workers.add(worker)
        worker.signals.finished.connect(self._on_finished)
        worker.signals.error.connect(self._on_error)
        worker.start()

    @Slot(object)
    def _on_finished(self, result: dict) -> None:
        self._start_btn.setEnabled(True)
        self._progress.complete()
        self._active_workers.discard(self._worker)
        self._worker = None

        self._summary_box.setVisible(True)
        self._summary_label.setText(
            f"Domain: {result.get('domain', '?')} / {result.get('subdomain', '?')}\n"
            f"Language: {result.get('language', '?')}\n"
            f"Entity types: {result.get('entity_types', 0)}\n"
            f"Relationship types: {result.get('relationship_types', 0)}\n"
            f"Prompts generated: {result.get('prompts_generated', 0)}\n"
            f"Documents analyzed: {result.get('documents_analyzed', 0)} "
            f"({result.get('total_pages', 0)} pages)\n"
            f"Ontology version: {result.get('version_id', '?')}"
        )
        logger.info("Bootstrap completed successfully")
        self.bootstrap_completed.emit(self._graph_name_input.text().strip())

    @Slot(str)
    def _on_error(self, traceback_str: str) -> None:
        self._start_btn.setEnabled(True)
        self._active_workers.discard(self._worker)
        self._worker = None
        self._append_log("ERROR", f"Bootstrap failed:\n{traceback_str}")
        QMessageBox.critical(self, "Bootstrap Failed", f"Error during bootstrap:\n\n{traceback_str}")

    def _append_log(self, level: str, message: str) -> None:
        self._log_area.appendPlainText(f"[{level}] {message}")

        # Heuristic: detect pipeline stage from log messages
        msg_lower = message.lower()
        if "analyzing domain" in msg_lower or "analyze" in msg_lower:
            self._progress.set_step(0)
        elif "discovering" in msg_lower or "discover" in msg_lower:
            self._progress.set_step(1)
        elif "refin" in msg_lower:
            self._progress.set_step(2)
        elif "generat" in msg_lower and "prompt" in msg_lower:
            self._progress.set_step(3)


