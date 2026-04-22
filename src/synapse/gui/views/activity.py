"""Activity log view — shows what each action (init, ingest, chat) added to the graph."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from synapse.gui.bridge import SynapseBridge

logger = logging.getLogger(__name__)

ACTION_ICONS = {
    "init": "\u2726",      # ✦
    "ingest": "\u21E9",    # ⇩
    "chat": "\u2709",      # ✉
}


class ActivityView(QWidget):
    """Timeline of actions with details of what each action added/changed."""

    def __init__(self, bridge: SynapseBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge

        header = QLabel("Activity Log")
        header.setFont(QFont("", 24, QFont.Weight.Bold))

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("secondary", True)
        refresh_btn.clicked.connect(self.refresh)

        header_row = QHBoxLayout()
        header_row.addWidget(header)
        header_row.addStretch()
        header_row.addWidget(refresh_btn)

        # Split: action list (left) + item details (right)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: action list
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_label = QLabel("Actions")
        left_label.setFont(QFont("", 14, QFont.Weight.Bold))
        left_layout.addWidget(left_label)

        self._action_list = QListWidget()
        self._action_list.currentItemChanged.connect(self._on_action_selected)
        left_layout.addWidget(self._action_list, stretch=1)

        # Right: item table
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._detail_header = QLabel("Select an action to view details")
        self._detail_header.setFont(QFont("", 14, QFont.Weight.Bold))
        right_layout.addWidget(self._detail_header)

        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet("color: #8e8ea0; font-size: 12px; padding: 4px 0;")
        right_layout.addWidget(self._summary_label)

        self._item_table = QTableWidget(0, 3)
        self._item_table.setHorizontalHeaderLabels(["Type", "Name", "Detail"])
        self._item_table.horizontalHeader().setStretchLastSection(True)
        self._item_table.setColumnWidth(0, 120)
        self._item_table.setColumnWidth(1, 300)
        self._item_table.setSortingEnabled(True)
        right_layout.addWidget(self._item_table, stretch=1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([300, 600])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addLayout(header_row)
        layout.addWidget(splitter, stretch=1)

    def refresh(self) -> None:
        self._action_list.clear()
        self._item_table.setRowCount(0)
        self._detail_header.setText("Select an action to view details")
        self._summary_label.setText("")

        try:
            store = self._bridge.get_store()
            actions = store.list_actions()
        except Exception as e:
            logger.error("Cannot load activity log: %s", e)
            return

        if not actions:
            self._action_list.addItem(QListWidgetItem("No activity recorded yet"))
            return

        for action in actions:
            icon = ACTION_ICONS.get(action["action_type"], "\u2022")
            label = action.get("action_label") or action["action_type"]
            count = action.get("item_count", 0)
            timestamp = (action.get("started_at") or "")[:16]

            text = f"{icon}  {label}  ({count} items)  {timestamp}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, action)
            self._action_list.addItem(item)

    @Slot()
    def _on_action_selected(self) -> None:
        item = self._action_list.currentItem()
        if not item:
            return
        action = item.data(Qt.ItemDataRole.UserRole)
        if not action:
            return

        action_type = action["action_type"]
        action_id = action["action_id"]
        label = action.get("action_label") or action_type

        try:
            store = self._bridge.get_store()
            items = store.get_action_items(action_type, action_id)
        except Exception as e:
            logger.error("Cannot load action items: %s", e)
            return

        self._detail_header.setText(label)

        # Summary
        entities = action.get("entities", 0)
        rels = action.get("relationships", 0)
        etypes = action.get("entity_types", 0)
        rtypes = action.get("rel_types", 0)
        prompts = action.get("prompts", 0)
        parts = []
        if etypes:
            parts.append(f"{etypes} entity types")
        if rtypes:
            parts.append(f"{rtypes} relationship types")
        if entities:
            parts.append(f"{entities} entities")
        if rels:
            parts.append(f"{rels} relationships")
        if prompts:
            parts.append(f"{prompts} prompts")
        self._summary_label.setText(" | ".join(parts) if parts else "")

        # Item table
        self._item_table.setSortingEnabled(False)
        self._item_table.setRowCount(len(items))
        for i, row in enumerate(items):
            self._item_table.setItem(i, 0, QTableWidgetItem(row.get("item_type", "")))
            self._item_table.setItem(i, 1, QTableWidgetItem(row.get("item_name", "")))
            self._item_table.setItem(i, 2, QTableWidgetItem(row.get("item_detail", "")))
        self._item_table.setSortingEnabled(True)
