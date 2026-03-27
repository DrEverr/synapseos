"""Versions view — replace ``synapse versions`` with a GUI table."""

from __future__ import annotations

import json
import logging

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from synapse.gui.bridge import SynapseBridge

logger = logging.getLogger(__name__)


class VersionsView(QWidget):
    """Ontology version management: list, activate, export."""

    def __init__(self, bridge: SynapseBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge

        header = QLabel("Ontology Versions")
        header.setFont(QFont("", 24, QFont.Weight.Bold))

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("secondary", True)
        refresh_btn.clicked.connect(self.refresh)

        header_row = QHBoxLayout()
        header_row.addWidget(header)
        header_row.addStretch()
        header_row.addWidget(refresh_btn)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "Version", "Name", "Domain", "Created At", "Active", "Actions"
        ])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 70)
        self._table.setColumnWidth(1, 200)
        self._table.setColumnWidth(2, 150)
        self._table.setColumnWidth(3, 180)
        self._table.setColumnWidth(4, 70)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addLayout(header_row)
        layout.addWidget(self._table, stretch=1)

    def refresh(self) -> None:
        try:
            store = self._bridge.get_store()
            versions = store.list_versions()
        except Exception as e:
            logger.error("Cannot load versions: %s", e)
            return

        self._table.setRowCount(len(versions))
        for i, v in enumerate(versions):
            vid = v["version_id"]
            self._table.setItem(i, 0, QTableWidgetItem(str(vid)))
            self._table.setItem(i, 1, QTableWidgetItem(v.get("name", "")))
            self._table.setItem(i, 2, QTableWidgetItem(v.get("domain", "")))
            self._table.setItem(i, 3, QTableWidgetItem(v.get("created_at", "")))

            active_text = "ACTIVE" if v.get("is_active") else ""
            active_item = QTableWidgetItem(active_text)
            if v.get("is_active"):
                active_item.setForeground(Qt.GlobalColor.green)
            self._table.setItem(i, 4, active_item)

            # Action buttons
            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(4, 4, 4, 4)
            actions_layout.setSpacing(6)

            activate_btn = QPushButton("Activate")
            activate_btn.setProperty("secondary", True)
            activate_btn.setStyleSheet(
                "QPushButton { padding: 4px 14px; font-size: 12px; }"
            )
            activate_btn.setEnabled(not v.get("is_active"))
            activate_btn.clicked.connect(lambda _, vid=vid: self._activate(vid))

            export_btn = QPushButton("Export")
            export_btn.setProperty("secondary", True)
            export_btn.setStyleSheet(
                "QPushButton { padding: 4px 14px; font-size: 12px; }"
            )
            export_btn.clicked.connect(lambda _, vid=vid: self._export(vid))

            actions_layout.addWidget(activate_btn)
            actions_layout.addWidget(export_btn)

            self._table.setCellWidget(i, 5, actions)
            self._table.setRowHeight(i, 50)

    @Slot()
    def _activate(self, version_id: int) -> None:
        try:
            store = self._bridge.get_store()
            store.activate_version(version_id)
            QMessageBox.information(self, "Success", f"Activated version {version_id}")
            self.refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to activate: {e}")

    @Slot()
    def _export(self, version_id: int) -> None:
        try:
            store = self._bridge.get_store()
            data = store.export_version(version_id)
            if not data:
                QMessageBox.warning(self, "Not Found", f"Version {version_id} not found.")
                return
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to export: {e}")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Ontology Version",
            f"ontology_v{version_id}.json",
            "JSON Files (*.json)",
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            QMessageBox.information(self, "Exported", f"Saved to {path}")
