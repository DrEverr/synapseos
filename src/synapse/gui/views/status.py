"""Status view — replace ``synapse status`` with a tabbed GUI.

Each category (Metadata, Entity Types, Relationship Types, Prompts) gets
its own full-size tab for comfortable browsing.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from synapse.gui.bridge import SynapseBridge

logger = logging.getLogger(__name__)


class StatusView(QWidget):
    """Instance status display with tabbed layout for each category."""

    def __init__(self, bridge: SynapseBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._prompts_data: dict[str, str] = {}

        # -- Header -----------------------------------------------------------
        header = QLabel("Instance Status")
        header.setFont(QFont("", 24, QFont.Weight.Bold))

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("secondary", True)
        refresh_btn.clicked.connect(self.refresh)

        header_row = QHBoxLayout()
        header_row.addWidget(header)
        header_row.addStretch()
        header_row.addWidget(refresh_btn)

        # -- Tabs -------------------------------------------------------------
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_metadata_tab(), "Metadata")
        self._tabs.addTab(self._build_entity_types_tab(), "Entity Types")
        self._tabs.addTab(self._build_rel_types_tab(), "Relationship Types")
        self._tabs.addTab(self._build_prompts_tab(), "Prompts")

        # -- Layout -----------------------------------------------------------
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addLayout(header_row)
        layout.addWidget(self._tabs, stretch=1)

    # -- Tab builders ---------------------------------------------------------

    def _build_metadata_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)

        # Metadata as a table for consistent sizing
        self._meta_table = QTableWidget(0, 2)
        self._meta_table.setHorizontalHeaderLabels(["Property", "Value"])
        self._meta_table.horizontalHeader().setStretchLastSection(True)
        self._meta_table.setColumnWidth(0, 220)
        self._meta_table.verticalHeader().setVisible(False)
        self._meta_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._meta_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)

        self._meta_fields = [
            ("Instance Directory", "instance_dir"),
            ("Graph Name", "graph_name"),
            ("Bootstrapped", "bootstrapped"),
            ("Domain", "domain"),
            ("Subdomain", "subdomain"),
            ("Language", "language"),
            ("Bootstrap Time", "bootstrap_time"),
            ("Active Ontology Version", "active_version"),
            ("Total Entity Types", "entity_count"),
            ("Total Relationship Types", "rel_count"),
            ("Total Prompts", "prompt_count"),
        ]

        layout.addWidget(self._meta_table, stretch=1)
        return tab

    def _build_entity_types_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)

        self._entity_count_label = QLabel("")
        self._entity_count_label.setStyleSheet("color: #8e8ea0; font-size: 12px;")

        self._entity_table = QTableWidget(0, 2)
        self._entity_table.setHorizontalHeaderLabels(["Type", "Description"])
        self._entity_table.horizontalHeader().setStretchLastSection(True)
        self._entity_table.setColumnWidth(0, 220)
        self._entity_table.setSortingEnabled(True)
        self._entity_table.setAlternatingRowColors(True)

        layout.addWidget(self._entity_count_label)
        layout.addWidget(self._entity_table, stretch=1)
        return tab

    def _build_rel_types_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)

        self._rel_count_label = QLabel("")
        self._rel_count_label.setStyleSheet("color: #8e8ea0; font-size: 12px;")

        self._rel_table = QTableWidget(0, 2)
        self._rel_table.setHorizontalHeaderLabels(["Type", "Description"])
        self._rel_table.horizontalHeader().setStretchLastSection(True)
        self._rel_table.setColumnWidth(0, 220)
        self._rel_table.setSortingEnabled(True)
        self._rel_table.setAlternatingRowColors(True)

        layout.addWidget(self._rel_count_label)
        layout.addWidget(self._rel_table, stretch=1)
        return tab

    def _build_prompts_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)

        self._prompt_count_label = QLabel("")
        self._prompt_count_label.setStyleSheet("color: #8e8ea0; font-size: 12px;")

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: prompt key list
        self._prompt_list = QListWidget()
        self._prompt_list.currentItemChanged.connect(self._on_prompt_selected)

        # Right: prompt text
        self._prompt_text = QPlainTextEdit()
        self._prompt_text.setReadOnly(True)
        self._prompt_text.setFont(QFont("Menlo", 11))
        self._prompt_text.setPlaceholderText("Select a prompt from the list to view its full text...")

        splitter.addWidget(self._prompt_list)
        splitter.addWidget(self._prompt_text)
        splitter.setSizes([250, 600])

        layout.addWidget(self._prompt_count_label)
        layout.addWidget(splitter, stretch=1)
        return tab

    # -- Data loading ---------------------------------------------------------

    def refresh(self) -> None:
        try:
            store = self._bridge.get_store()
            s = self._bridge.settings
        except Exception as e:
            logger.error("Cannot load store: %s", e)
            return

        # -- Metadata tab (table) --
        bootstrapped = store.is_bootstrapped()
        active_vid = store.get_active_version_id()
        etypes = store.get_entity_types()
        rtypes = store.get_relationship_types()
        prompts = store.get_all_prompts()

        meta_rows = [
            ("Instance Directory", str(s.get_instance_dir())),
            ("Graph Name", s.graph_name),
            ("Bootstrapped", "Yes" if bootstrapped else "No"),
            ("Domain", store.get_meta("domain", "—")),
            ("Subdomain", store.get_meta("subdomain", "—")),
            ("Language", store.get_meta("language", "—")),
            ("Bootstrap Time", store.get_meta("bootstrap_timestamp", "—")),
            ("Active Ontology Version", str(active_vid) if active_vid else "—"),
            ("Total Entity Types", str(len(etypes))),
            ("Total Relationship Types", str(len(rtypes))),
            ("Total Prompts", str(len(prompts))),
        ]
        self._meta_table.setRowCount(len(meta_rows))
        for i, (prop, val) in enumerate(meta_rows):
            self._meta_table.setItem(i, 0, QTableWidgetItem(prop))
            self._meta_table.setItem(i, 1, QTableWidgetItem(val))

        # -- Entity types tab --
        self._entity_count_label.setText(f"{len(etypes)} entity types defined")
        self._entity_table.setSortingEnabled(False)
        self._entity_table.setRowCount(len(etypes))
        for i, (etype, desc) in enumerate(sorted(etypes.items())):
            self._entity_table.setItem(i, 0, QTableWidgetItem(etype))
            self._entity_table.setItem(i, 1, QTableWidgetItem(desc))
        self._entity_table.setSortingEnabled(True)

        # -- Relationship types tab --
        self._rel_count_label.setText(f"{len(rtypes)} relationship types defined")
        self._rel_table.setSortingEnabled(False)
        self._rel_table.setRowCount(len(rtypes))
        for i, (rtype, desc) in enumerate(sorted(rtypes.items())):
            self._rel_table.setItem(i, 0, QTableWidgetItem(rtype))
            self._rel_table.setItem(i, 1, QTableWidgetItem(desc))
        self._rel_table.setSortingEnabled(True)

        # -- Prompts tab --
        self._prompts_data = prompts
        self._prompt_count_label.setText(f"{len(prompts)} AI-generated prompts")
        self._prompt_list.clear()
        self._prompt_text.clear()
        for key in sorted(prompts.keys()):
            text = prompts[key]
            item = QListWidgetItem(f"{key}  ({len(text)} chars)")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._prompt_list.addItem(item)

    @Slot()
    def _on_prompt_selected(self) -> None:
        item = self._prompt_list.currentItem()
        if not item:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        text = self._prompts_data.get(key, "")
        self._prompt_text.setPlainText(text)
