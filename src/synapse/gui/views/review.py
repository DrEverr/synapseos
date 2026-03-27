"""Review view — accept or reject unverified AI-generated entities and relationships."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from synapse.gui.bridge import SynapseBridge

logger = logging.getLogger(__name__)


class ReviewView(QWidget):
    """Review and accept/reject unverified AI-generated graph items."""

    def __init__(self, bridge: SynapseBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge

        header = QLabel("Review Unverified Items")
        header.setFont(QFont("", 24, QFont.Weight.Bold))

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #8e8ea0; font-size: 13px;")

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("secondary", True)
        refresh_btn.clicked.connect(self.refresh)

        header_row = QHBoxLayout()
        header_row.addWidget(header)
        header_row.addStretch()
        header_row.addWidget(self._status_label)
        header_row.addWidget(refresh_btn)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_entities_tab(), "Entities")
        self._tabs.addTab(self._build_relationships_tab(), "Relationships")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addLayout(header_row)
        layout.addWidget(self._tabs, stretch=1)

    def _build_entities_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)

        btn_row = QHBoxLayout()
        accept_btn = QPushButton("Accept Selected")
        accept_btn.clicked.connect(self._accept_entities)
        reject_btn = QPushButton("Reject Selected")
        reject_btn.setStyleSheet("QPushButton { background-color: #ef4444; }")
        reject_btn.clicked.connect(self._reject_entities)
        accept_all_btn = QPushButton("Accept All")
        accept_all_btn.setProperty("secondary", True)
        accept_all_btn.clicked.connect(self._accept_all_entities)
        btn_row.addWidget(accept_btn)
        btn_row.addWidget(reject_btn)
        btn_row.addStretch()
        btn_row.addWidget(accept_all_btn)

        self._entity_table = QTableWidget(0, 4)
        self._entity_table.setHorizontalHeaderLabels(["Name", "Type", "Confidence", "Source"])
        self._entity_table.horizontalHeader().setStretchLastSection(True)
        self._entity_table.setColumnWidth(0, 250)
        self._entity_table.setColumnWidth(1, 150)
        self._entity_table.setColumnWidth(2, 80)
        self._entity_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._entity_table.setSelectionMode(QTableWidget.SelectionMode.MultiSelection)

        layout.addLayout(btn_row)
        layout.addWidget(self._entity_table, stretch=1)
        return tab

    def _build_relationships_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)

        btn_row = QHBoxLayout()
        accept_btn = QPushButton("Accept Selected")
        accept_btn.clicked.connect(self._accept_relationships)
        reject_btn = QPushButton("Reject Selected")
        reject_btn.setStyleSheet("QPushButton { background-color: #ef4444; }")
        reject_btn.clicked.connect(self._reject_relationships)
        accept_all_btn = QPushButton("Accept All")
        accept_all_btn.setProperty("secondary", True)
        accept_all_btn.clicked.connect(self._accept_all_relationships)
        btn_row.addWidget(accept_btn)
        btn_row.addWidget(reject_btn)
        btn_row.addStretch()
        btn_row.addWidget(accept_all_btn)

        self._rel_table = QTableWidget(0, 5)
        self._rel_table.setHorizontalHeaderLabels(["Subject", "Predicate", "Object", "Confidence", "Source"])
        self._rel_table.horizontalHeader().setStretchLastSection(True)
        self._rel_table.setColumnWidth(0, 180)
        self._rel_table.setColumnWidth(1, 150)
        self._rel_table.setColumnWidth(2, 180)
        self._rel_table.setColumnWidth(3, 80)
        self._rel_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._rel_table.setSelectionMode(QTableWidget.SelectionMode.MultiSelection)

        layout.addLayout(btn_row)
        layout.addWidget(self._rel_table, stretch=1)
        return tab

    # -- Data loading ---------------------------------------------------------

    def refresh(self) -> None:
        try:
            graph = self._bridge.get_graph()
        except Exception as e:
            logger.error("Cannot connect to FalkorDB: %s", e)
            self._status_label.setText("Cannot connect to FalkorDB")
            return

        entities = graph.get_unverified_entities()
        rels = graph.get_unverified_relationships()

        self._status_label.setText(
            f"{len(entities)} entities, {len(rels)} relationships pending review"
        )

        self._entity_table.setRowCount(len(entities))
        for i, row in enumerate(entities):
            self._entity_table.setItem(i, 0, QTableWidgetItem("" if row[0] is None else str(row[0])))
            self._entity_table.setItem(i, 1, QTableWidgetItem("" if row[1] is None else str(row[1])))
            self._entity_table.setItem(i, 2, QTableWidgetItem("" if row[2] is None else str(row[2])))
            self._entity_table.setItem(i, 3, QTableWidgetItem("" if row[3] is None else str(row[3])))

        self._rel_table.setRowCount(len(rels))
        for i, row in enumerate(rels):
            self._rel_table.setItem(i, 0, QTableWidgetItem("" if row[0] is None else str(row[0])))
            self._rel_table.setItem(i, 1, QTableWidgetItem("" if row[1] is None else str(row[1])))
            self._rel_table.setItem(i, 2, QTableWidgetItem("" if row[2] is None else str(row[2])))
            self._rel_table.setItem(i, 3, QTableWidgetItem("" if row[3] is None else str(row[3])))
            self._rel_table.setItem(i, 4, QTableWidgetItem("" if row[4] is None else str(row[4])))

    # -- Entity actions -------------------------------------------------------

    def _get_graph(self):
        return self._bridge.get_graph()

    def _accept_entities(self) -> None:
        rows = set(idx.row() for idx in self._entity_table.selectedIndexes())
        if not rows:
            return
        graph = self._get_graph()
        for row in rows:
            name = self._entity_table.item(row, 0).text()
            etype = self._entity_table.item(row, 1).text()
            if name and etype:
                graph.verify_entity(name, etype)
        self.refresh()

    def _reject_entities(self) -> None:
        rows = set(idx.row() for idx in self._entity_table.selectedIndexes())
        if not rows:
            return
        count = len(rows)
        if QMessageBox.question(
            self, "Confirm Reject",
            f"Delete {count} unverified entity(ies) and their relationships?",
        ) != QMessageBox.StandardButton.Yes:
            return
        graph = self._get_graph()
        for row in rows:
            name = self._entity_table.item(row, 0).text()
            etype = self._entity_table.item(row, 1).text()
            if name and etype:
                graph.reject_entity(name, etype)
        self.refresh()

    def _accept_all_entities(self) -> None:
        graph = self._get_graph()
        graph.verify_all_entities()
        self.refresh()

    # -- Relationship actions -------------------------------------------------

    def _accept_relationships(self) -> None:
        rows = set(idx.row() for idx in self._rel_table.selectedIndexes())
        if not rows:
            return
        graph = self._get_graph()
        for row in rows:
            subj = self._rel_table.item(row, 0).text()
            pred = self._rel_table.item(row, 1).text()
            obj = self._rel_table.item(row, 2).text()
            if subj and pred and obj:
                graph.verify_relationship(subj, pred, obj)
        self.refresh()

    def _reject_relationships(self) -> None:
        rows = set(idx.row() for idx in self._rel_table.selectedIndexes())
        if not rows:
            return
        count = len(rows)
        if QMessageBox.question(
            self, "Confirm Reject",
            f"Delete {count} unverified relationship(s)?",
        ) != QMessageBox.StandardButton.Yes:
            return
        graph = self._get_graph()
        for row in rows:
            subj = self._rel_table.item(row, 0).text()
            pred = self._rel_table.item(row, 1).text()
            obj = self._rel_table.item(row, 2).text()
            if subj and pred and obj:
                graph.reject_relationship(subj, pred, obj)
        self.refresh()

    def _accept_all_relationships(self) -> None:
        graph = self._get_graph()
        graph.verify_all_relationships()
        self.refresh()
