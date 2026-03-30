"""Graph Inspector view — replace ``synapse inspect`` with a tabbed GUI."""

from __future__ import annotations

import json
import logging

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from synapse.gui.bridge import SynapseBridge

logger = logging.getLogger(__name__)


class _StatCard(QGroupBox):
    def __init__(self, title: str, value: str = "0") -> None:
        super().__init__(title)
        self.setMinimumWidth(160)
        self.setMinimumHeight(80)
        self._value = QLabel(value)
        self._value.setFont(QFont("", 24, QFont.Weight.Bold))
        self._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.addWidget(self._value)

    def set_value(self, v: str) -> None:
        self._value.setText(v)


class GraphInspectorView(QWidget):
    """Tabbed graph inspector: Overview, Triples, Trees, Cypher, Duplicates."""

    def __init__(self, bridge: SynapseBridge, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._bridge = bridge

        header = QLabel("Graph Inspector")
        header.setFont(QFont("", 24, QFont.Weight.Bold))

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setProperty("secondary", True)
        refresh_btn.clicked.connect(self.refresh)

        header_row = QHBoxLayout()
        header_row.addWidget(header)
        header_row.addStretch()
        header_row.addWidget(refresh_btn)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_overview_tab(), "Overview")
        self._tabs.addTab(self._build_health_tab(), "Health")
        self._tabs.addTab(self._build_triples_tab(), "Triples")
        self._tabs.addTab(self._build_trees_tab(), "Document Trees")
        self._tabs.addTab(self._build_cypher_tab(), "Cypher")
        self._tabs.addTab(self._build_duplicates_tab(), "Duplicates")
        self._tabs.addTab(self._build_conflicts_tab(), "Conflicts")
        self._tabs.addTab(self._build_decayed_tab(), "Decayed")
        self._tabs.addTab(self._build_provenance_tab(), "Provenance")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addLayout(header_row)
        layout.addWidget(self._tabs, stretch=1)

    # -- Tab builders ---------------------------------------------------------

    def _build_overview_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self._card_nodes = _StatCard("Nodes")
        self._card_edges = _StatCard("Edges")

        cards = QHBoxLayout()
        cards.addWidget(self._card_nodes)
        cards.addWidget(self._card_edges)
        cards.addStretch()

        self._entity_table = QTableWidget(0, 2)
        self._entity_table.setHorizontalHeaderLabels(["Entity Type", "Count"])
        self._entity_table.horizontalHeader().setStretchLastSection(True)
        self._entity_table.setSortingEnabled(True)

        self._rel_table = QTableWidget(0, 2)
        self._rel_table.setHorizontalHeaderLabels(["Relationship Type", "Count"])
        self._rel_table.horizontalHeader().setStretchLastSection(True)
        self._rel_table.setSortingEnabled(True)

        layout.addLayout(cards)
        layout.addWidget(QLabel("Entity Types:"))
        layout.addWidget(self._entity_table, stretch=1)
        layout.addWidget(QLabel("Relationship Types:"))
        layout.addWidget(self._rel_table, stretch=1)
        return tab

    def _build_triples_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        toolbar = QHBoxLayout()
        self._triples_limit = QSpinBox()
        self._triples_limit.setRange(10, 10000)
        self._triples_limit.setValue(100)
        toolbar.addWidget(QLabel("Limit:"))
        toolbar.addWidget(self._triples_limit)
        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self._load_triples)
        toolbar.addWidget(load_btn)
        toolbar.addStretch()

        self._triples_table = QTableWidget(0, 5)
        self._triples_table.setHorizontalHeaderLabels([
            "Subject", "Subject Type", "Predicate", "Object", "Object Type"
        ])
        self._triples_table.horizontalHeader().setStretchLastSection(True)
        self._triples_table.setSortingEnabled(True)

        layout.addLayout(toolbar)
        layout.addWidget(self._triples_table, stretch=1)
        return tab

    def _build_trees_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        load_btn = QPushButton("Load Document Trees")
        load_btn.clicked.connect(self._load_trees)

        self._doc_tree = QTreeWidget()
        self._doc_tree.setHeaderLabels(["Section", "Pages"])
        self._doc_tree.setColumnWidth(0, 400)
        self._doc_tree.itemDoubleClicked.connect(self._on_tree_item_double_clicked)

        layout.addWidget(load_btn)
        layout.addWidget(self._doc_tree, stretch=1)
        return tab

    def _build_cypher_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layout.addWidget(QLabel("Enter a read-only Cypher query:"))

        self._cypher_input = QPlainTextEdit()
        self._cypher_input.setMaximumHeight(120)
        self._cypher_input.setPlaceholderText(
            "MATCH (n:PROTOCOL)-[r]->(m) RETURN n.name, type(r), m.name LIMIT 50"
        )

        exec_btn = QPushButton("Execute")
        exec_btn.clicked.connect(self._execute_cypher)

        self._cypher_result = QTableWidget()
        self._cypher_result.setColumnCount(0)

        layout.addWidget(self._cypher_input)
        layout.addWidget(exec_btn)
        layout.addWidget(self._cypher_result, stretch=1)
        return tab

    def _build_health_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        load_btn = QPushButton("Run Health Check")
        load_btn.clicked.connect(self._load_health)

        # Stat cards row
        self._health_cards_layout = QGridLayout()
        self._health_labels: dict[str, QLabel] = {}
        metrics = [
            ("Entities", "entity_count"),
            ("Orphan Nodes", "orphan_nodes"),
            ("Low-Conf Entities", "low_confidence_entities"),
            ("Low-Conf Rels", "low_confidence_relationships"),
            ("Unverified", "unverified_count"),
            ("Avg Confidence", "avg_confidence"),
            ("Rel Density", "relationship_density"),
            ("Doc Coverage", "document_coverage_pct"),
        ]
        for i, (title, key) in enumerate(metrics):
            card = _StatCard(title, "—")
            self._health_labels[key] = card._value
            self._health_cards_layout.addWidget(card, i // 4, i % 4)

        self._health_unused_label = QLabel("")
        self._health_unused_label.setWordWrap(True)
        self._health_unused_label.setStyleSheet("color: #8e8ea0; padding: 8px;")

        layout.addWidget(load_btn)
        layout.addLayout(self._health_cards_layout)
        layout.addWidget(self._health_unused_label)
        layout.addStretch()
        return tab

    def _build_conflicts_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        load_btn = QPushButton("Detect Conflicts")
        load_btn.clicked.connect(self._load_conflicts)

        self._conflicts_table = QTableWidget(0, 6)
        self._conflicts_table.setHorizontalHeaderLabels([
            "Subject", "Relation 1", "Relation 2", "Object", "Conf 1", "Conf 2"
        ])
        self._conflicts_table.horizontalHeader().setStretchLastSection(True)
        self._conflicts_table.setSortingEnabled(True)

        self._conflicts_count = QLabel("")

        layout.addWidget(load_btn)
        layout.addWidget(self._conflicts_count)
        layout.addWidget(self._conflicts_table, stretch=1)
        return tab

    def _build_decayed_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        load_btn = QPushButton("Find Decayed Entities")
        load_btn.clicked.connect(self._load_decayed)

        self._decayed_table = QTableWidget(0, 5)
        self._decayed_table.setHorizontalHeaderLabels([
            "Entity", "Type", "Base Conf", "Effective Conf", "Last Confirmed"
        ])
        self._decayed_table.horizontalHeader().setStretchLastSection(True)
        self._decayed_table.setSortingEnabled(True)

        self._decayed_count = QLabel("")

        layout.addWidget(load_btn)
        layout.addWidget(self._decayed_count)
        layout.addWidget(self._decayed_table, stretch=1)
        return tab

    def _build_provenance_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        toolbar = QHBoxLayout()
        self._provenance_input = QLineEdit()
        self._provenance_input.setPlaceholderText("Entity name to look up...")
        self._provenance_input.returnPressed.connect(self._load_provenance)
        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self._load_provenance)
        toolbar.addWidget(self._provenance_input, stretch=1)
        toolbar.addWidget(search_btn)

        self._provenance_table = QTableWidget(0, 5)
        self._provenance_table.setHorizontalHeaderLabels([
            "Entity", "Type", "Source Text", "Section", "Document"
        ])
        self._provenance_table.horizontalHeader().setStretchLastSection(True)
        self._provenance_table.setWordWrap(True)

        layout.addLayout(toolbar)
        layout.addWidget(self._provenance_table, stretch=1)
        return tab

    def _build_duplicates_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        load_btn = QPushButton("Find Duplicates")
        load_btn.clicked.connect(self._load_duplicates)

        self._dup_table = QTableWidget(0, 3)
        self._dup_table.setHorizontalHeaderLabels(["Name", "Type", "Count"])
        self._dup_table.horizontalHeader().setStretchLastSection(True)
        self._dup_table.setSortingEnabled(True)

        layout.addWidget(load_btn)
        layout.addWidget(self._dup_table, stretch=1)
        return tab

    # -- Data loading ---------------------------------------------------------

    def refresh(self) -> None:
        """Reload overview data from the graph."""
        try:
            graph = self._bridge.get_graph()
        except Exception as e:
            logger.error("Cannot connect to FalkorDB: %s", e)
            return

        self._card_nodes.set_value(str(graph.get_node_count()))
        self._card_edges.set_value(str(graph.get_edge_count()))

        entity_counts = graph.get_entity_counts()
        self._entity_table.setRowCount(len(entity_counts))
        for i, (etype, count) in enumerate(entity_counts.items()):
            self._entity_table.setItem(i, 0, QTableWidgetItem("" if etype is None else str(etype)))
            count_item = QTableWidgetItem()
            count_item.setData(Qt.ItemDataRole.DisplayRole, int(count or 0))
            self._entity_table.setItem(i, 1, count_item)

        rel_counts = graph.get_relationship_counts()
        self._rel_table.setRowCount(len(rel_counts))
        for i, (rtype, count) in enumerate(rel_counts.items()):
            self._rel_table.setItem(i, 0, QTableWidgetItem(rtype))
            item = QTableWidgetItem()
            item.setData(Qt.ItemDataRole.DisplayRole, int(count or 0))
            self._rel_table.setItem(i, 1, item)

    @Slot()
    def _load_triples(self) -> None:
        try:
            graph = self._bridge.get_graph()
            limit = self._triples_limit.value()
            triples = graph.get_all_triples(limit=limit)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load triples: {e}")
            return

        self._triples_table.setRowCount(len(triples))
        for i, (subj, stype, pred, obj, otype) in enumerate(triples):
            self._triples_table.setItem(i, 0, QTableWidgetItem("" if subj is None else str(subj)))
            self._triples_table.setItem(i, 1, QTableWidgetItem("" if stype is None else str(stype)))
            self._triples_table.setItem(i, 2, QTableWidgetItem("" if pred is None else str(pred)))
            self._triples_table.setItem(i, 3, QTableWidgetItem("" if obj is None else str(obj)))
            self._triples_table.setItem(i, 4, QTableWidgetItem("" if otype is None else str(otype)))

    @Slot()
    def _load_trees(self) -> None:
        try:
            graph = self._bridge.get_graph()
            docs = graph.get_documents()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load documents: {e}")
            return

        self._doc_tree.clear()
        for doc in docs:
            doc_id = doc.get("id", "")
            doc_item = QTreeWidgetItem([
                f"{doc.get('title', '?')} ({doc.get('filename', '?')})",
                f"{doc.get('page_count', '?')} pages",
            ])
            tree_json = doc.get("tree_json", "[]")
            try:
                sections = json.loads(tree_json) if tree_json else []
                self._build_section_tree(doc_item, sections, doc_id)
            except json.JSONDecodeError:
                pass
            self._doc_tree.addTopLevelItem(doc_item)
            doc_item.setExpanded(True)

    def _build_section_tree(self, parent: QTreeWidgetItem, sections: list, doc_id: str) -> None:
        for section in sections:
            title = section.get("title", "Unknown")
            pages = f"pp. {section.get('start_page', '?')}-{section.get('end_page', '?')}"
            item = QTreeWidgetItem([title, pages])
            # Full section_id = doc_hash:node_id (matches text cache key)
            node_id = section.get("node_id", "")
            section_id = f"{doc_id}:{node_id}" if doc_id and node_id else ""
            item.setData(0, Qt.ItemDataRole.UserRole, section_id)
            parent.addChild(item)
            children = section.get("children", [])
            if children:
                self._build_section_tree(item, children, doc_id)

    @Slot()
    def _execute_cypher(self) -> None:
        query = self._cypher_input.toPlainText().strip()
        if not query:
            return

        try:
            graph = self._bridge.get_graph()
            result = graph.query(query)
        except Exception as e:
            QMessageBox.warning(self, "Query Error", str(e))
            return

        if not result:
            self._cypher_result.setRowCount(0)
            self._cypher_result.setColumnCount(1)
            self._cypher_result.setHorizontalHeaderLabels(["Result"])
            self._cypher_result.setRowCount(1)
            self._cypher_result.setItem(0, 0, QTableWidgetItem("(no results)"))
            return

        ncols = len(result[0]) if result else 0
        self._cypher_result.setColumnCount(ncols)

        # Extract column names from RETURN clause
        headers = self._parse_return_columns(query, ncols)
        self._cypher_result.setHorizontalHeaderLabels(headers)

        self._cypher_result.setRowCount(len(result))
        for i, row in enumerate(result):
            for j, cell in enumerate(row):
                display = "" if cell is None else str(cell)
                self._cypher_result.setItem(i, j, QTableWidgetItem(display))

        # Auto-resize columns to content
        self._cypher_result.resizeColumnsToContents()

    def _collect_child_section_ids(self, item: QTreeWidgetItem) -> list[str]:
        """Recursively collect section_ids from all children of a tree item."""
        ids = []
        for i in range(item.childCount()):
            child = item.child(i)
            sid = child.data(0, Qt.ItemDataRole.UserRole)
            if sid:
                ids.append(sid)
            ids.extend(self._collect_child_section_ids(child))
        return ids

    def _on_tree_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """Open a dialog showing section details and cached text (rendered as Markdown)."""
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        from synapse.gui.widgets.chat_bubble import markdown_to_html

        title = item.text(0)
        pages = item.text(1)

        # Try to load section text from text cache
        section_text = ""
        try:
            from synapse.storage.text_cache import TextCache
            s = self._bridge.settings
            cache = TextCache(cache_dir=s.get_text_cache_dir())
            section_id = item.data(0, Qt.ItemDataRole.UserRole)

            if section_id:
                section_text = cache.get(section_id) or ""

            # If no text for this node, collect from children (parent sections)
            if not section_text:
                child_ids = self._collect_child_section_ids(item)
                parts = []
                for cid in child_ids:
                    t = cache.get(cid)
                    if t:
                        parts.append(t)
                if parts:
                    section_text = "\n\n---\n\n".join(parts)
        except Exception:
            pass

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Section — {title}")
        dialog.setFixedSize(700, 500)

        from PySide6.QtWidgets import QTextBrowser

        scroll = QTextBrowser()
        scroll.setOpenExternalLinks(True)
        scroll.setStyleSheet("padding: 12px; font-size: 13px; color: #e4e4ed; background-color: #1a1a22; border: none;")

        if section_text:
            # Strip <page_N> tags from cached text
            import re as _re
            section_text = _re.sub(r"</?page_\d+>", "", section_text).strip()
            # Render as Markdown HTML
            header_md = f"## {title}\n\n*{pages}*\n\n---\n\n"
            html = markdown_to_html(header_md + section_text)
            scroll.setHtml(html)
        else:
            scroll.setPlainText(f"Section: {title}\nPages: {pages}\n\n(no cached text available)")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.close)

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.addWidget(scroll, stretch=1)
        dlg_layout.addWidget(buttons)
        dialog.exec()

    @staticmethod
    def _parse_return_columns(query: str, ncols: int) -> list[str]:
        """Extract column names from a Cypher RETURN clause."""
        import re
        match = re.search(r'\bRETURN\b\s+(.+?)(?:\s+ORDER\b|\s+LIMIT\b|\s+SKIP\b|\s*$)', query, re.IGNORECASE | re.DOTALL)
        if match:
            raw = match.group(1)
            parts = [p.strip() for p in raw.split(",")]
            headers = []
            for p in parts:
                # Use AS alias if present
                alias_match = re.search(r'\bAS\s+(\w+)\s*$', p, re.IGNORECASE)
                if alias_match:
                    headers.append(alias_match.group(1))
                else:
                    # Use the expression itself, cleaned up
                    headers.append(p.split(".")[-1].strip())
            if len(headers) == ncols:
                return headers
        return [f"Col {i}" for i in range(ncols)]

    @Slot()
    def _load_duplicates(self) -> None:
        try:
            graph = self._bridge.get_graph()
            dupes = graph.find_duplicates()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to find duplicates: {e}")
            return

        self._dup_table.setRowCount(len(dupes))
        for i, (name, label, count) in enumerate(dupes):
            self._dup_table.setItem(i, 0, QTableWidgetItem("" if name is None else str(name)))
            self._dup_table.setItem(i, 1, QTableWidgetItem("" if label is None else str(label)))
            item = QTableWidgetItem()
            item.setData(Qt.ItemDataRole.DisplayRole, int(count or 0))
            self._dup_table.setItem(i, 2, item)

    @Slot()
    def _load_health(self) -> None:
        try:
            graph = self._bridge.get_graph()
            store = self._bridge.get_store()
            from synapse.config import OntologyRegistry
            ontology = OntologyRegistry(store=store, ontology_name=self._bridge.settings.ontology)
            report = graph.get_graph_health(ontology_types=ontology.entity_types)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Health check failed: {e}")
            return

        self._health_labels["entity_count"].setText(str(report["entity_count"]))
        self._health_labels["orphan_nodes"].setText(str(report["orphan_nodes"]))
        self._health_labels["low_confidence_entities"].setText(str(report["low_confidence_entities"]))
        self._health_labels["low_confidence_relationships"].setText(str(report["low_confidence_relationships"]))
        self._health_labels["unverified_count"].setText(str(report["unverified_count"]))
        self._health_labels["avg_confidence"].setText(f"{report['avg_confidence']:.3f}")
        self._health_labels["relationship_density"].setText(f"{report['relationship_density']:.2f}")
        self._health_labels["document_coverage_pct"].setText(
            f"{report['document_coverage_pct']}%"
        )
        if report["unused_ontology_types"]:
            self._health_unused_label.setText(
                f"Unused ontology types: {', '.join(report['unused_ontology_types'])}"
            )
        else:
            self._health_unused_label.setText("All ontology types are in use.")

    @Slot()
    def _load_conflicts(self) -> None:
        try:
            graph = self._bridge.get_graph()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Cannot connect: {e}")
            return

        from pathlib import Path
        import json as _json
        rules_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "conflict_rules.json"
        rules: list[list[str]] = []
        if rules_path.exists():
            try:
                rules_data = _json.loads(rules_path.read_text())
                rules = rules_data.get("contradictory_pairs", [])
            except (_json.JSONDecodeError, ValueError) as e:
                logger.warning("Failed to parse %s: %s — using defaults", rules_path, e)
                rules = []
        if not rules:
            rules = [
                ["CAUSES", "PROTECTS_AGAINST"],
                ["COMPATIBLE_WITH", "INCOMPATIBLE_WITH"],
                ["SUITABLE_FOR", "INEFFECTIVE_AGAINST"],
                ["VULNERABLE_TO", "PROTECTS_AGAINST"],
            ]

        conflicts = graph.find_conflicts(rules)
        self._conflicts_count.setText(
            f"Found {len(conflicts)} conflict(s)." if conflicts else "No conflicts found."
        )
        self._conflicts_table.setRowCount(len(conflicts))
        for i, c in enumerate(conflicts):
            self._conflicts_table.setItem(i, 0, QTableWidgetItem(str(c["subject"] or "")))
            self._conflicts_table.setItem(i, 1, QTableWidgetItem(str(c["rel1"] or "")))
            self._conflicts_table.setItem(i, 2, QTableWidgetItem(str(c["rel2"] or "")))
            self._conflicts_table.setItem(i, 3, QTableWidgetItem(str(c["object"] or "")))
            conf1 = QTableWidgetItem()
            conf1.setData(Qt.ItemDataRole.DisplayRole, float(c.get("confidence1") or 0))
            self._conflicts_table.setItem(i, 4, conf1)
            conf2 = QTableWidgetItem()
            conf2.setData(Qt.ItemDataRole.DisplayRole, float(c.get("confidence2") or 0))
            self._conflicts_table.setItem(i, 5, conf2)
        self._conflicts_table.resizeColumnsToContents()

    @Slot()
    def _load_decayed(self) -> None:
        try:
            graph = self._bridge.get_graph()
            settings = self._bridge.settings
            rows = graph.get_decayed_entities(
                decay_rate=settings.confidence_decay_rate, threshold=0.5
            )
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load decayed entities: {e}")
            return

        self._decayed_count.setText(
            f"Found {len(rows)} decayed entit{'y' if len(rows) == 1 else 'ies'}."
            if rows else "No decayed entities found."
        )
        self._decayed_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._decayed_table.setItem(i, 0, QTableWidgetItem(str(row[0] or "")))
            self._decayed_table.setItem(i, 1, QTableWidgetItem(str(row[1] or "")))
            base = QTableWidgetItem()
            base.setData(Qt.ItemDataRole.DisplayRole, float(row[2] or 0))
            self._decayed_table.setItem(i, 2, base)
            eff = QTableWidgetItem()
            eff.setData(Qt.ItemDataRole.DisplayRole, float(row[4] or 0))
            self._decayed_table.setItem(i, 3, eff)
            self._decayed_table.setItem(i, 4, QTableWidgetItem(str(row[3] or "")))
        self._decayed_table.resizeColumnsToContents()

    @Slot()
    def _load_provenance(self) -> None:
        query = self._provenance_input.text().strip()
        if not query:
            return
        try:
            graph = self._bridge.get_graph()
            results = graph.get_entity_provenance(query)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Provenance lookup failed: {e}")
            return

        # If no source_text, try text cache fallback
        cache = None
        try:
            from synapse.storage.text_cache import TextCache
            cache = TextCache(cache_dir=self._bridge.settings.get_text_cache_dir())
        except Exception as e:
            logger.debug("Could not initialize TextCache for provenance fallback: %s", e)

        self._provenance_table.setRowCount(len(results))
        for i, r in enumerate(results):
            self._provenance_table.setItem(i, 0, QTableWidgetItem(r["entity"] or ""))
            self._provenance_table.setItem(i, 1, QTableWidgetItem(r["entity_type"] or ""))
            source = r["source_text"]
            if not source and r.get("section_id") and cache is not None:
                try:
                    ctx = cache.get_context(r["section_id"], query)
                    source = ctx[:300] if ctx else ""
                except Exception as e:
                    logger.debug("TextCache fallback failed for %s: %s", r["section_id"], e)
            self._provenance_table.setItem(i, 2, QTableWidgetItem(source))
            self._provenance_table.setItem(i, 3, QTableWidgetItem(r["section_title"] or ""))
            self._provenance_table.setItem(i, 4, QTableWidgetItem(
                f"{r['doc_title']} ({r['doc_filename']})" if r.get("doc_title") else ""
            ))
        self._provenance_table.resizeColumnsToContents()
