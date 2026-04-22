"""Dashboard view — landing page with stats, quick actions, sessions, and insights."""

from __future__ import annotations

import random

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class _StatCard(QFrame):
    """A small card displaying a label + value."""

    def __init__(self, title: str, value: str = "—", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setMinimumWidth(140)
        self.setStyleSheet("""
            #StatCard {
                background-color: #1a1a22;
                border: 1px solid #2a2a38;
                border-radius: 12px;
            }
            #StatCard QLabel {
                background-color: transparent;
            }
        """)
        self._title_label = QLabel(title.upper())
        self._title_label.setFont(QFont("", 10, QFont.Weight.Bold))
        self._title_label.setStyleSheet("color: #55556a; letter-spacing: 1px; background: transparent;")

        self._value_label = QLabel(value)
        self._value_label.setFont(QFont("", 22, QFont.Weight.Bold))
        self._value_label.setStyleSheet("color: #e4e4ed; background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)
        layout.addWidget(self._title_label)
        layout.addWidget(self._value_label)

    def set_value(self, value: str) -> None:
        self._value_label.setText(value)


class _InsightCard(QFrame):
    """A single insight / fun-fact card with icon and text."""

    def __init__(self, icon: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1a1a2e, stop:1 #1a1a22);
                border: 1px solid #2a2a38;
                border-radius: 12px;
            }
        """)
        icon_label = QLabel(icon)
        icon_label.setFont(QFont("", 22))
        icon_label.setFixedWidth(40)
        icon_label.setStyleSheet("background: transparent; border: none;")

        text_label = QLabel(text)
        text_label.setWordWrap(True)
        text_label.setStyleSheet("color: #e4e4ed; font-size: 13px; background: transparent; border: none;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)
        layout.addWidget(icon_label, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addWidget(text_label, stretch=1)


class DashboardView(QWidget):
    """Main dashboard — stats, quick actions, sessions, and live insights."""

    navigate_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # -- Header -----------------------------------------------------------
        header = QLabel("Dashboard")
        header.setFont(QFont("", 24, QFont.Weight.Bold))

        self._subtitle = QLabel("Select a graph to get started")
        self._subtitle.setStyleSheet("color: #8e8ea0; font-size: 13px;")

        # -- Stat cards -------------------------------------------------------
        self._card_domain = _StatCard("Domain")
        self._card_nodes = _StatCard("Nodes")
        self._card_edges = _StatCard("Edges")
        self._card_status = _StatCard("Status")

        cards_layout = QGridLayout()
        cards_layout.setSpacing(12)
        cards_layout.addWidget(self._card_domain, 0, 0)
        cards_layout.addWidget(self._card_nodes, 0, 1)
        cards_layout.addWidget(self._card_edges, 0, 2)
        cards_layout.addWidget(self._card_status, 0, 3)

        # -- Quick actions ----------------------------------------------------
        actions_label = QLabel("Quick Actions")
        actions_label.setFont(QFont("", 16, QFont.Weight.Bold))

        btn_init = QPushButton("  Init New Domain  ")
        btn_ingest = QPushButton("  Ingest Documents  ")
        btn_chat = QPushButton("  Open Chat  ")

        btn_init.clicked.connect(lambda: self.navigate_requested.emit("init"))
        btn_ingest.clicked.connect(lambda: self.navigate_requested.emit("ingest"))
        btn_chat.clicked.connect(lambda: self.navigate_requested.emit("chat"))

        actions_row = QHBoxLayout()
        actions_row.setSpacing(12)
        actions_row.addWidget(btn_init)
        actions_row.addWidget(btn_ingest)
        actions_row.addWidget(btn_chat)
        actions_row.addStretch()

        # -- Bottom half: Insights + Sessions side by side --------------------
        bottom_splitter = QHBoxLayout()
        bottom_splitter.setSpacing(16)

        # Insights panel (left)
        insights_frame = QFrame()
        insights_frame.setStyleSheet("""
            QFrame { background: transparent; }
        """)
        insights_inner = QVBoxLayout(insights_frame)
        insights_inner.setContentsMargins(0, 0, 0, 0)
        insights_inner.setSpacing(8)

        insights_header = QLabel("Insights")
        insights_header.setFont(QFont("", 16, QFont.Weight.Bold))
        insights_inner.addWidget(insights_header)

        self._insights_container = QVBoxLayout()
        self._insights_container.setSpacing(8)
        insights_inner.addLayout(self._insights_container)
        insights_inner.addStretch()

        # Sessions panel (right)
        sessions_frame = QFrame()
        sessions_frame.setStyleSheet("QFrame { background: transparent; }")
        sessions_inner = QVBoxLayout(sessions_frame)
        sessions_inner.setContentsMargins(0, 0, 0, 0)

        sessions_label = QLabel("Recent Sessions")
        sessions_label.setFont(QFont("", 16, QFont.Weight.Bold))

        self._sessions_list = QListWidget()
        self._sessions_list.setMaximumHeight(250)

        sessions_inner.addWidget(sessions_label)
        sessions_inner.addWidget(self._sessions_list, stretch=1)
        sessions_inner.addStretch()

        bottom_splitter.addWidget(insights_frame, stretch=3)
        bottom_splitter.addWidget(sessions_frame, stretch=2)

        # -- Main layout ------------------------------------------------------
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addWidget(header)
        layout.addWidget(self._subtitle)
        layout.addLayout(cards_layout)
        layout.addWidget(actions_label)
        layout.addLayout(actions_row)
        layout.addLayout(bottom_splitter, stretch=1)

    # -- Public ---------------------------------------------------------------

    @Slot(dict)
    def update_data(self, data: dict) -> None:
        """Refresh the dashboard with data from SynapseBridge.get_dashboard_data()."""
        self._subtitle.setText(
            f"Graph: {data.get('graph_name', '—')}  |  {data.get('instance_dir', '')}"
        )

        self._card_domain.set_value(data.get("domain", "—"))
        self._card_nodes.set_value(str(data.get("node_count", 0)))
        self._card_edges.set_value(str(data.get("edge_count", 0)))

        bootstrapped = data.get("bootstrapped", False)
        self._card_status.set_value("Ready" if bootstrapped else "Not initialized")
        self._card_status._value_label.setStyleSheet(
            f"color: {'#2dd4a8' if bootstrapped else '#f0a030'}; font-size: 22px; font-weight: bold; background: transparent;"
        )

        # Sessions
        self._sessions_list.clear()
        sessions = data.get("sessions", [])
        if sessions:
            for s in sessions[:10]:
                name = s.get("name") or s.get("session_id", "")[:8]
                turns = s.get("episode_count", 0)
                started = (s.get("started_at") or "")[:16]
                item = QListWidgetItem(f"{name}  —  {turns} turns  —  {started}")
                self._sessions_list.addItem(item)
        else:
            self._sessions_list.addItem(QListWidgetItem("No sessions yet"))

        # Insights
        self._update_insights(data)

    def _update_insights(self, data: dict) -> None:
        """Pick one random insight and display it."""
        # Clear existing
        while self._insights_container.count():
            item = self._insights_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        insights = self._generate_insights(data)
        if insights:
            icon, text = random.choice(insights)
            self._insights_container.addWidget(_InsightCard(icon, text))

    def _generate_insights(self, data: dict) -> list[tuple[str, str]]:
        """Build a list of (icon, text) insight tuples from available data."""
        insights: list[tuple[str, str]] = []
        nodes = data.get("node_count", 0)
        edges = data.get("edge_count", 0)
        sessions = data.get("sessions", [])
        domain = data.get("domain", "—")

        # Total sessions
        total_sessions = len(sessions)
        if total_sessions > 0:
            total_turns = sum(s.get("episode_count", 0) for s in sessions)
            insights.append((
                "\U0001F4AC",  # 💬
                f"You have {total_sessions} chat session{'s' if total_sessions != 1 else ''} "
                f"with a total of {total_turns} question{'s' if total_turns != 1 else ''} asked."
            ))

        # Graph density
        if nodes > 0 and edges > 0:
            density = edges / nodes
            insights.append((
                "\U0001F578",  # 🕸
                f"Your knowledge graph has a density of {density:.1f} edges per node. "
                f"{'That is a richly connected graph!' if density > 3 else 'Adding more documents will increase connectivity.'}"
            ))

        # Documents processed
        doc_count = data.get("doc_count", 0)
        total_pages = data.get("total_pages", 0)
        if doc_count > 0:
            insights.append((
                "\U0001F4DA",  # 📚
                f"{doc_count} document{'s' if doc_count != 1 else ''} processed "
                f"({total_pages} pages total)."
            ))

        # Entity types & relationship types
        entity_type_count = data.get("entity_type_count", 0)
        rel_type_count = data.get("rel_type_count", 0)
        if entity_type_count > 0:
            insights.append((
                "\U0001F9E9",  # 🧩
                f"The ontology defines {entity_type_count} entity types and "
                f"{rel_type_count} relationship types for the {domain} domain."
            ))

        # Random triple
        random_triples = data.get("random_triples", [])
        if random_triples:
            subj, pred, obj = random.choice(random_triples)
            insights.append((
                "\U0001F517",  # 🔗
                f"Did you know? {subj} {pred} {obj}"
            ))

        # Deep chain (2-hop relationship)
        deep_chain = data.get("deep_chain")
        if deep_chain:
            insights.append((
                "\U0001F50D",  # 🔍
                f"Multi-hop path: {deep_chain}"
            ))

        # Latest relationship
        latest_rel = data.get("latest_relationship")
        if latest_rel:
            insights.append((
                "\U00002728",  # ✨
                f"Recently extracted: {latest_rel}"
            ))

        # Fallback if no insights
        if not insights:
            if data.get("bootstrapped"):
                insights.append((
                    "\U0001F680",  # 🚀
                    "Your instance is ready! Ingest some documents to start building the knowledge graph."
                ))
            else:
                insights.append((
                    "\U0001F44B",  # 👋
                    "Welcome to SynapseOS! Start by selecting a graph and running Init to bootstrap your domain."
                ))

        return insights
