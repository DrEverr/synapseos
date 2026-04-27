"""Review view — accept or reject unverified AI-generated entities, relationships, and new types."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
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

        header = QLabel("Review")
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
        self._tabs.addTab(self._build_triples_tab(), "Triples")
        self._tabs.addTab(self._build_types_tab(), "New Types")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addLayout(header_row)
        layout.addWidget(self._tabs, stretch=1)

    # -- Tab builders ---------------------------------------------------------

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

        splitter = QSplitter(Qt.Orientation.Vertical)

        self._entity_table = QTableWidget(0, 4)
        self._entity_table.setHorizontalHeaderLabels(["Name", "Type", "Confidence", "Source"])
        self._entity_table.horizontalHeader().setStretchLastSection(True)
        self._entity_table.setColumnWidth(0, 250)
        self._entity_table.setColumnWidth(1, 150)
        self._entity_table.setColumnWidth(2, 80)
        self._entity_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._entity_table.setSelectionMode(QTableWidget.SelectionMode.MultiSelection)
        self._entity_table.currentCellChanged.connect(self._on_entity_selected)
        self._entity_table.doubleClicked.connect(lambda idx: self._show_full_context("entity", idx.row()))

        self._entity_context = QTextBrowser()
        self._entity_context.setMaximumHeight(150)
        self._entity_context.setStyleSheet(
            "font-size: 12px; color: #e4e4ed; background-color: #1a1a22; border: 1px solid #2a2a38; border-radius: 6px; padding: 8px;"
        )
        self._entity_context.setPlaceholderText("Select an entity to see its context...")

        splitter.addWidget(self._entity_table)
        splitter.addWidget(self._entity_context)
        splitter.setSizes([300, 100])

        layout.addLayout(btn_row)
        layout.addWidget(splitter, stretch=1)
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

    def _build_triples_tab(self) -> QWidget:
        """New tab: full unverified triples with context."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)

        btn_row = QHBoxLayout()
        accept_btn = QPushButton("Accept Selected")
        accept_btn.clicked.connect(self._accept_triples)
        reject_btn = QPushButton("Reject Selected")
        reject_btn.setStyleSheet("QPushButton { background-color: #ef4444; }")
        reject_btn.clicked.connect(self._reject_triples)
        btn_row.addWidget(accept_btn)
        btn_row.addWidget(reject_btn)
        btn_row.addStretch()

        splitter = QSplitter(Qt.Orientation.Vertical)

        self._triple_table = QTableWidget(0, 6)
        self._triple_table.setHorizontalHeaderLabels([
            "Subject", "Type", "Predicate", "Object", "Type", "Source"
        ])
        self._triple_table.horizontalHeader().setStretchLastSection(True)
        self._triple_table.setColumnWidth(0, 180)
        self._triple_table.setColumnWidth(1, 100)
        self._triple_table.setColumnWidth(2, 140)
        self._triple_table.setColumnWidth(3, 180)
        self._triple_table.setColumnWidth(4, 100)
        self._triple_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._triple_table.setSelectionMode(QTableWidget.SelectionMode.MultiSelection)
        self._triple_table.currentCellChanged.connect(self._on_triple_selected)
        self._triple_table.doubleClicked.connect(lambda idx: self._show_full_context("triple", idx.row()))

        self._triple_context = QTextBrowser()
        self._triple_context.setMaximumHeight(150)
        self._triple_context.setStyleSheet(
            "font-size: 12px; color: #e4e4ed; background-color: #1a1a22; border: 1px solid #2a2a38; border-radius: 6px; padding: 8px;"
        )
        self._triple_context.setPlaceholderText("Select a triple to see its context...")

        splitter.addWidget(self._triple_table)
        splitter.addWidget(self._triple_context)
        splitter.setSizes([300, 100])

        layout.addLayout(btn_row)
        layout.addWidget(splitter, stretch=1)
        return tab

    def _build_types_tab(self) -> QWidget:
        """New tab: entity types and relationship types from the latest init/ingest."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)

        # Entity types
        layout.addWidget(QLabel("Entity Types (current ontology):"))
        self._etypes_table = QTableWidget(0, 2)
        self._etypes_table.setHorizontalHeaderLabels(["Type", "Description"])
        self._etypes_table.horizontalHeader().setStretchLastSection(True)
        self._etypes_table.setColumnWidth(0, 220)

        layout.addWidget(self._etypes_table, stretch=1)

        # Relationship types
        layout.addWidget(QLabel("Relationship Types (current ontology):"))
        self._rtypes_table = QTableWidget(0, 2)
        self._rtypes_table.setHorizontalHeaderLabels(["Type", "Description"])
        self._rtypes_table.horizontalHeader().setStretchLastSection(True)
        self._rtypes_table.setColumnWidth(0, 220)

        layout.addWidget(self._rtypes_table, stretch=1)
        return tab

    # -- Data loading ---------------------------------------------------------

    def refresh(self) -> None:
        try:
            graph = self._bridge.get_graph()
        except Exception as e:
            logger.error("Cannot connect to FalkorDB: %s", e)
            self._status_label.setText("Cannot connect to FalkorDB")
            return

        self._load_entities(graph)
        self._load_relationships(graph)
        self._load_triples(graph)
        self._load_types()

    def _load_entities(self, graph) -> None:
        entities = graph.get_unverified_entities()
        self._entity_table.setRowCount(len(entities))
        for i, row in enumerate(entities):
            self._entity_table.setItem(i, 0, QTableWidgetItem("" if row[0] is None else str(row[0])))
            self._entity_table.setItem(i, 1, QTableWidgetItem("" if row[1] is None else str(row[1])))
            self._entity_table.setItem(i, 2, QTableWidgetItem("" if row[2] is None else str(row[2])))
            self._entity_table.setItem(i, 3, QTableWidgetItem("" if row[3] is None else str(row[3])))
        self._entity_context.clear()
        self._update_status()

    def _load_relationships(self, graph) -> None:
        rels = graph.get_unverified_relationships()
        self._rel_table.setRowCount(len(rels))
        for i, row in enumerate(rels):
            self._rel_table.setItem(i, 0, QTableWidgetItem("" if row[0] is None else str(row[0])))
            self._rel_table.setItem(i, 1, QTableWidgetItem("" if row[1] is None else str(row[1])))
            self._rel_table.setItem(i, 2, QTableWidgetItem("" if row[2] is None else str(row[2])))
            self._rel_table.setItem(i, 3, QTableWidgetItem("" if row[3] is None else str(row[3])))
            self._rel_table.setItem(i, 4, QTableWidgetItem("" if row[4] is None else str(row[4])))

    def _load_triples(self, graph) -> None:
        """Load unverified triples with full subject/object types."""
        rows = graph.query(
            "MATCH (a)-[r]->(b) "
            "WHERE COALESCE(r.verified, true) = false "
            "AND NOT a:Document AND NOT a:Section "
            "AND NOT b:Document AND NOT b:Section "
            "RETURN a.canonical_name, labels(a)[0], type(r), "
            "b.canonical_name, labels(b)[0], r.source_doc "
            "ORDER BY type(r), a.canonical_name"
        )
        self._triple_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j in range(6):
                self._triple_table.setItem(i, j, QTableWidgetItem("" if row[j] is None else str(row[j])))
        self._triple_context.clear()

    def _load_types(self) -> None:
        """Load entity/relationship types from current ontology."""
        try:
            store = self._bridge.get_store()
            from synapse.config import OntologyRegistry
            ontology = OntologyRegistry(store=store, ontology_name=self._bridge.settings.ontology)

            etypes = ontology.entity_types
            self._etypes_table.setRowCount(len(etypes))
            for i, (name, desc) in enumerate(sorted(etypes.items())):
                self._etypes_table.setItem(i, 0, QTableWidgetItem(name))
                item = QTableWidgetItem(desc)
                item.setToolTip(desc)
                self._etypes_table.setItem(i, 1, item)

            rtypes = ontology.relationship_types
            self._rtypes_table.setRowCount(len(rtypes))
            for i, (name, desc) in enumerate(sorted(rtypes.items())):
                self._rtypes_table.setItem(i, 0, QTableWidgetItem(name))
                item = QTableWidgetItem(desc)
                item.setToolTip(desc)
                self._rtypes_table.setItem(i, 1, item)
        except Exception as e:
            logger.error("Failed to load types: %s", e)

    def _update_status(self) -> None:
        ent_count = self._entity_table.rowCount()
        rel_count = self._rel_table.rowCount()
        triple_count = self._triple_table.rowCount()
        self._status_label.setText(
            f"{ent_count} entities, {rel_count} relationships, {triple_count} triples pending"
        )

    # -- Context panels -------------------------------------------------------

    def _get_enrichment_episode(self, entity_name: str) -> dict | None:
        """Return the full reasoning episode that produced an enrichment entity."""
        try:
            store = self._bridge.get_store()
            row = store._conn.execute(
                "SELECT action_label, created_at FROM activity_log "
                "WHERE action_type = 'chat' AND item_name = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (entity_name,),
            ).fetchone()
            if not row:
                return None
            ep = store._conn.execute(
                "SELECT question, answer FROM reasoning_episodes "
                "WHERE (entities_added > 0 OR rels_added > 0) "
                "AND created_at <= ? "
                "ORDER BY created_at DESC LIMIT 1",
                (row["created_at"],),
            ).fetchone()
            if ep:
                return {"label": row["action_label"], "question": ep["question"], "answer": ep["answer"]}
        except Exception:
            pass
        return None

    def _show_full_context(self, table_type: str, row: int) -> None:
        """Open a dialog with full untruncated context for the selected item."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        from synapse.gui.widgets.chat_bubble import markdown_to_html

        if table_type == "entity":
            table = self._entity_table
            name = table.item(row, 0).text() if table.item(row, 0) else ""
            etype = table.item(row, 1).text() if table.item(row, 1) else ""
            title = f"{name} [{etype}]"
        else:
            table = self._triple_table
            subj = table.item(row, 0).text() if table.item(row, 0) else ""
            pred = table.item(row, 2).text() if table.item(row, 2) else ""
            obj = table.item(row, 3).text() if table.item(row, 3) else ""
            name = subj
            title = f"{subj} -[{pred}]-> {obj}"

        if not name:
            return

        ep = self._get_enrichment_episode(name)
        if not ep:
            return

        # Get source_text from graph node or activity log
        source_quote = ""
        try:
            graph = self._bridge.get_graph()
            rows = graph.query(
                "MATCH (n) WHERE n.canonical_name = $name AND n.source_text IS NOT NULL "
                "RETURN n.source_text LIMIT 1",
                params={"name": name},
            )
            if rows and rows[0][0]:
                source_quote = rows[0][0]
            if not source_quote:
                store = self._bridge.get_store()
                row = store._conn.execute(
                    "SELECT item_detail FROM activity_log "
                    "WHERE action_type = 'chat' AND item_name = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (name,),
                ).fetchone()
                if row and row["item_detail"] and len(row["item_detail"]) > 20:
                    source_quote = row["item_detail"]
        except Exception:
            pass

        # Highlight source_quote in the answer
        answer_html = ep["answer"]
        if source_quote and source_quote in answer_html:
            answer_html = answer_html.replace(
                source_quote, f"**>>>{source_quote}<<<**"
            )

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Context — {title}")
        dialog.setMinimumSize(700, 500)

        content = QTextBrowser()
        content.setOpenExternalLinks(True)
        content.setStyleSheet(
            "padding: 12px; font-size: 13px; color: #e4e4ed; "
            "background-color: #1a1a22; border: none;"
        )
        md = f"## {title}\n\n**Source:** {ep['label']}\n\n"
        if source_quote:
            md += f"**Extracted from:** \"{source_quote}\"\n\n"
        md += f"---\n\n**Question:**\n\n{ep['question']}\n\n---\n\n"
        md += f"**Answer:**\n\n{answer_html}"
        content.setHtml(markdown_to_html(md))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.addWidget(content, stretch=1)
        dlg_layout.addWidget(buttons)
        dialog.exec()

    def _get_enrichment_context(self, entity_name: str) -> list[str]:
        """Find the chat Q&A that produced an enrichment entity via activity log."""
        lines = []
        try:
            store = self._bridge.get_store()
            # Find in activity log which chat action added this entity
            row = store._conn.execute(
                "SELECT action_label, created_at FROM activity_log "
                "WHERE action_type = 'chat' AND item_name = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (entity_name,),
            ).fetchone()
            if not row:
                return lines

            action_label = row["action_label"]  # e.g. "Chat: competitors comparison"
            created_at = row["created_at"]

            # Find the reasoning episode closest in time
            ep = store._conn.execute(
                "SELECT question, answer FROM reasoning_episodes "
                "WHERE (entities_added > 0 OR rels_added > 0) "
                "AND created_at <= ? "
                "ORDER BY created_at DESC LIMIT 1",
                (created_at,),
            ).fetchone()
            # Get source_text (the exact quote from the answer)
            source_quote = ""
            detail_row = store._conn.execute(
                "SELECT item_detail FROM activity_log "
                "WHERE action_type = 'chat' AND item_name = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (entity_name,),
            ).fetchone()
            if detail_row and detail_row["item_detail"] and len(detail_row["item_detail"]) > 20:
                source_quote = detail_row["item_detail"]

            if ep:
                lines.append(f"<br><b>Chat context</b> ({action_label}):")
                if source_quote:
                    lines.append(f"<b>Extracted from:</b> <i>\"{source_quote}\"</i>")
                lines.append(f"<b>Q:</b> {ep['question']}")
                answer_preview = ep["answer"][:300]
                if len(ep["answer"]) > 300:
                    answer_preview += "..."
                lines.append(f"<b>A:</b> {answer_preview}")
        except Exception as e:
            logger.debug("Failed to get enrichment context: %s", e)
        return lines

    @Slot(int, int, int, int)
    def _on_entity_selected(self, row: int, col: int, prev_row: int, prev_col: int) -> None:
        if row < 0:
            return
        name = self._entity_table.item(row, 0)
        etype = self._entity_table.item(row, 1)
        source = self._entity_table.item(row, 3)
        if not name:
            return

        lines = [f"<b>{name.text()}</b> [{etype.text() if etype else '?'}]"]
        lines.append(f"Source: {source.text() if source else '?'}")

        # Try to get provenance / source_text from graph
        try:
            graph = self._bridge.get_graph()
            provenance = graph.get_entity_provenance(name.text())
            if provenance:
                for p in provenance[:3]:
                    if p.get("source_text"):
                        lines.append(f"<br><i>\"{p['source_text']}\"</i>")
                    lines.append(f"Section: {p.get('section_title', '?')} | Doc: {p.get('doc_title', '?')}")

            # Show chat Q&A context for enrichment items
            source_text = source.text() if source else ""
            if "enrichment" in source_text:
                lines.extend(self._get_enrichment_context(name.text()))

            # Show neighbors
            neighbors = graph.get_neighbors(name.text(), max_hops=1)
            if neighbors:
                lines.append("<br><b>Connections:</b>")
                seen = set()
                for n in neighbors[:8]:
                    path = n[0] if n[0] else []
                    rels = n[1] if n[1] else []
                    key = str(path) + str(rels)
                    if key not in seen:
                        seen.add(key)
                        path_str = " → ".join(str(p) for p in path)
                        rel_str = ", ".join(str(r) for r in rels)
                        lines.append(f"  {path_str} ({rel_str})")
        except Exception:
            pass

        self._entity_context.setHtml("<br>".join(lines))

    @Slot(int, int, int, int)
    def _on_triple_selected(self, row: int, col: int, prev_row: int, prev_col: int) -> None:
        if row < 0:
            return
        subj = self._triple_table.item(row, 0)
        stype = self._triple_table.item(row, 1)
        pred = self._triple_table.item(row, 2)
        obj = self._triple_table.item(row, 3)
        otype = self._triple_table.item(row, 4)
        source = self._triple_table.item(row, 5)

        lines = [
            f"<b>{subj.text() if subj else '?'}</b> [{stype.text() if stype else '?'}]",
            f"  —[{pred.text() if pred else '?'}]→",
            f"<b>{obj.text() if obj else '?'}</b> [{otype.text() if otype else '?'}]",
            f"<br>Source: {source.text() if source else '?'}",
        ]

        # Chat Q&A context for enrichment triples
        source_text = source.text() if source else ""
        if "enrichment" in source_text and subj:
            lines.extend(self._get_enrichment_context(subj.text()))

        # Try to get provenance for both subject and object
        try:
            graph = self._bridge.get_graph()
            for entity_name, label in [(subj, "Subject"), (obj, "Object")]:
                if not entity_name:
                    continue
                prov = graph.get_entity_provenance(entity_name.text())
                if prov:
                    p = prov[0]
                    if p.get("source_text"):
                        lines.append(f"<br><b>{label}:</b> <i>\"{p['source_text']}\"</i>")
                    lines.append(f"  Doc: {p.get('doc_title', '?')} / {p.get('section_title', '?')}")
        except Exception:
            pass

        self._triple_context.setHtml("<br>".join(lines))

    # -- Entity actions -------------------------------------------------------

    def _get_graph(self):
        return self._bridge.get_graph()

    def _accept_entities(self) -> None:
        rows = sorted({idx.row() for idx in self._entity_table.selectedIndexes()})
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
        rows = sorted({idx.row() for idx in self._entity_table.selectedIndexes()})
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
        rows = sorted({idx.row() for idx in self._rel_table.selectedIndexes()})
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
        rows = sorted({idx.row() for idx in self._rel_table.selectedIndexes()})
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

    # -- Triple actions -------------------------------------------------------

    def _accept_triples(self) -> None:
        rows = sorted({idx.row() for idx in self._triple_table.selectedIndexes()})
        if not rows:
            return
        graph = self._get_graph()
        for row in rows:
            subj = self._triple_table.item(row, 0).text()
            pred = self._triple_table.item(row, 2).text()
            obj = self._triple_table.item(row, 3).text()
            if subj and pred and obj:
                graph.verify_relationship(subj, pred, obj)
        self.refresh()

    def _reject_triples(self) -> None:
        rows = sorted({idx.row() for idx in self._triple_table.selectedIndexes()})
        if not rows:
            return
        count = len(rows)
        if QMessageBox.question(
            self, "Confirm Reject",
            f"Delete {count} unverified triple(s)?",
        ) != QMessageBox.StandardButton.Yes:
            return
        graph = self._get_graph()
        for row in rows:
            subj = self._triple_table.item(row, 0).text()
            pred = self._triple_table.item(row, 2).text()
            obj = self._triple_table.item(row, 3).text()
            if subj and pred and obj:
                graph.delete_relationship(subj, pred, obj)
        self.refresh()
