"""SynapseOS GUI — main application window.

Entry point: ``synapse-gui`` (or ``python -m synapse.gui.app``).

Uses a custom sidebar panel (full-width nav buttons), a QStackedWidget for
views, and a QDockWidget log panel at the bottom.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from synapse.gui.bridge import SynapseBridge
from synapse.gui.theme import apply_theme
from synapse.gui.views.activity import ActivityView
from synapse.gui.views.bootstrap import BootstrapView
from synapse.gui.views.chat import ChatView
from synapse.gui.views.dashboard import DashboardView
from synapse.gui.views.graph import GraphInspectorView
from synapse.gui.views.ingest import IngestView
from synapse.gui.views.review import ReviewView
from synapse.gui.views.status import StatusView
from synapse.gui.views.versions import VersionsView
from synapse.gui.widgets.log_panel import LogPanel
from synapse.gui.workers import LogSignalHandler

logger = logging.getLogger(__name__)

# View indices
VIEW_DASHBOARD = 0
VIEW_INIT = 1
VIEW_INGEST = 2
VIEW_CHAT = 3
VIEW_GRAPH = 4
VIEW_STATUS = 5
VIEW_VERSIONS = 6
VIEW_ACTIVITY = 7
VIEW_REVIEW = 8

NAV_MAP = {
    "dashboard": VIEW_DASHBOARD,
    "init": VIEW_INIT,
    "ingest": VIEW_INGEST,
    "chat": VIEW_CHAT,
    "graph": VIEW_GRAPH,
    "status": VIEW_STATUS,
    "versions": VIEW_VERSIONS,
    "activity": VIEW_ACTIVITY,
    "review": VIEW_REVIEW,
}

NAV_BUTTON_INACTIVE = """
    QPushButton {
        background-color: transparent;
        color: #8e8ea0;
        border: none;
        border-radius: 8px;
        padding: 11px 14px;
        text-align: left;
        font-size: 13px;
        font-weight: 500;
    }
    QPushButton:hover {
        background-color: #1a1a22;
        color: #e4e4ed;
    }
"""

NAV_BUTTON_ACTIVE = """
    QPushButton {
        background-color: rgba(124, 92, 252, 0.12);
        color: #9b7dff;
        border: none;
        border-left: 3px solid #7c5cfc;
        border-radius: 0px 8px 8px 0px;
        padding: 11px 14px;
        text-align: left;
        font-size: 13px;
        font-weight: 600;
    }
    QPushButton:hover {
        background-color: rgba(124, 92, 252, 0.18);
    }
"""


class MainWindow(QMainWindow):
    """Top-level application window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SynapseOS")
        self.resize(1200, 800)

        self._bridge = SynapseBridge()

        # -- Log panel (dock, bottom) -----------------------------------------
        self._log_panel = LogPanel(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._log_panel)

        self._log_handler = LogSignalHandler()
        self._log_handler.bridge.log_record.connect(self._log_panel.append_log)
        logging.getLogger().addHandler(self._log_handler)
        logging.getLogger().setLevel(logging.DEBUG)

        # -- Central layout: sidebar + stack ----------------------------------
        central = QWidget()
        central_layout = QHBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        self.setCentralWidget(central)

        # -- Sidebar panel ----------------------------------------------------
        sidebar = QWidget()
        sidebar.setFixedWidth(180)
        sidebar.setStyleSheet("background-color: #0d0d12; border-right: 1px solid #1a1a22;")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(6, 16, 6, 12)
        sidebar_layout.setSpacing(2)

        # Logo
        logo = QLabel("\u2B21  SynapseOS")
        logo.setFont(QFont("", 14, QFont.Weight.Bold))
        logo.setStyleSheet(
            "color: #e4e4ed; padding: 4px 14px 16px 10px; background: transparent;"
        )
        sidebar_layout.addWidget(logo)

        # Section label
        nav_label = QLabel("NAVIGATION")
        nav_label.setStyleSheet(
            "color: #55556a; font-size: 10px; font-weight: 700; "
            "letter-spacing: 1.5px; padding: 4px 14px 6px 14px; background: transparent;"
        )
        sidebar_layout.addWidget(nav_label)

        # Nav buttons
        nav_items = [
            ("Dashboard", VIEW_DASHBOARD),
            ("Init", VIEW_INIT),
            ("Ingest", VIEW_INGEST),
            ("Chat", VIEW_CHAT),
            ("Graph", VIEW_GRAPH),
            ("Status", VIEW_STATUS),
            ("Versions", VIEW_VERSIONS),
            ("Activity", VIEW_ACTIVITY),
            ("Review", VIEW_REVIEW),
        ]

        nav_icons = {
            "Dashboard": "\u2302",    # ⌂
            "Init": "\u2726",         # ✦
            "Ingest": "\u21E9",       # ⇩
            "Chat": "\u2709",         # ✉
            "Graph": "\u2B21",        # ⬡
            "Status": "\u2139",       # ℹ
            "Versions": "\u29C1",     # ⧁
            "Activity": "\u29D6",     # ⧖
            "Review": "\u2714",      # ✔
        }

        self._nav_buttons: list[QPushButton] = []
        for label, view_idx in nav_items:
            icon = nav_icons.get(label, "")
            btn = QPushButton(f"  {icon}   {label}")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setStyleSheet(NAV_BUTTON_INACTIVE)
            btn.clicked.connect(lambda _, idx=view_idx: self._select_view(idx))
            sidebar_layout.addWidget(btn)
            self._nav_buttons.append(btn)

        sidebar_layout.addStretch()

        # Graph selector at bottom of sidebar
        gs_section = QLabel("GRAPH")
        gs_section.setStyleSheet(
            "color: #55556a; font-size: 10px; font-weight: 700; "
            "letter-spacing: 1.5px; padding: 4px 14px 4px 14px; background: transparent;"
        )
        sidebar_layout.addWidget(gs_section)

        self._graph_combo = QComboBox()
        self._graph_combo.currentTextChanged.connect(self._on_graph_changed)
        sidebar_layout.addWidget(self._graph_combo)

        central_layout.addWidget(sidebar)

        # -- Stacked widget ---------------------------------------------------
        self._stack = QStackedWidget()
        central_layout.addWidget(self._stack, stretch=1)

        # -- Create all views -------------------------------------------------
        self._dashboard = DashboardView()
        self._dashboard.navigate_requested.connect(self._navigate_to)

        self._bootstrap_view = BootstrapView(self._bridge)
        self._bootstrap_view.bootstrap_completed.connect(self._on_bootstrap_done)
        self._ingest_view = IngestView(self._bridge)
        self._chat_view = ChatView(self._bridge)
        self._graph_view = GraphInspectorView(self._bridge)
        self._status_view = StatusView(self._bridge)
        self._versions_view = VersionsView(self._bridge)
        self._activity_view = ActivityView(self._bridge)
        self._review_view = ReviewView(self._bridge)

        self._stack.addWidget(self._dashboard)          # 0
        self._stack.addWidget(self._bootstrap_view)     # 1
        self._stack.addWidget(self._ingest_view)        # 2
        self._stack.addWidget(self._chat_view)          # 3
        self._stack.addWidget(self._graph_view)         # 4
        self._stack.addWidget(self._status_view)        # 5
        self._stack.addWidget(self._versions_view)      # 6
        self._stack.addWidget(self._activity_view)      # 7
        self._stack.addWidget(self._review_view)        # 8

        # -- Menu bar -------------------------------------------------------------
        self._build_menu_bar()

        # -- Initial state ----------------------------------------------------
        self._select_view(VIEW_DASHBOARD)
        self._refresh_graph_list()
        self._refresh_dashboard()

    # -- Menu bar -------------------------------------------------------------

    def _build_menu_bar(self) -> None:
        menu_bar = self.menuBar()
        menu_bar.setStyleSheet("""
            QMenuBar {
                background-color: #0d0d12;
                color: #8e8ea0;
                border-bottom: 1px solid #1a1a22;
                padding: 2px 0;
            }
            QMenuBar::item {
                padding: 6px 12px;
                border-radius: 4px;
            }
            QMenuBar::item:selected {
                background-color: #222230;
                color: #e4e4ed;
            }
            QMenu {
                background-color: #1a1a22;
                color: #e4e4ed;
                border: 1px solid #2a2a38;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 24px 6px 12px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #7c5cfc;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background-color: #2a2a38;
                margin: 4px 8px;
            }
        """)

        # -- File menu --
        file_menu = menu_bar.addMenu("File")

        file_menu.addAction(
            self._make_action("New Session", "Ctrl+N", lambda: (
                self._select_view(VIEW_CHAT),
                self._chat_view.new_session(),
            ))
        )
        file_menu.addSeparator()
        file_menu.addAction(
            self._make_action("Quit", "Ctrl+Q", QApplication.quit)
        )

        # -- View menu --
        view_menu = menu_bar.addMenu("View")

        for label, idx in [
            ("Dashboard", VIEW_DASHBOARD), ("Init", VIEW_INIT),
            ("Ingest", VIEW_INGEST), ("Chat", VIEW_CHAT),
            ("Graph Inspector", VIEW_GRAPH), ("Status", VIEW_STATUS),
            ("Versions", VIEW_VERSIONS),
        ]:
            view_menu.addAction(
                self._make_action(label, None, lambda _, i=idx: self._select_view(i))
            )

        view_menu.addSeparator()

        self._toggle_logs_action = QAction("Show Logs", self)
        self._toggle_logs_action.setCheckable(True)
        self._toggle_logs_action.setChecked(self._log_panel.isVisible())
        self._toggle_logs_action.setShortcut(QKeySequence("Ctrl+L"))
        self._toggle_logs_action.toggled.connect(self._log_panel.setVisible)
        self._log_panel.visibilityChanged.connect(self._toggle_logs_action.setChecked)
        view_menu.addAction(self._toggle_logs_action)

        # -- Window menu --
        window_menu = menu_bar.addMenu("Window")
        window_menu.addAction(
            self._make_action("Minimize", "Ctrl+M", self.showMinimized)
        )
        window_menu.addAction(
            self._make_action("Zoom", None, lambda: (
                self.showNormal() if self.isMaximized() else self.showMaximized()
            ))
        )

        # -- Help menu --
        help_menu = menu_bar.addMenu("Help")
        help_menu.addAction(
            self._make_action("About SynapseOS", None, self._show_about)
        )

    def _make_action(self, text: str, shortcut: str | None, callback) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        return action

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About SynapseOS",
            "<h3>SynapseOS</h3>"
            "<p>Domain-Agnostic Knowledge Operating System</p>"
            "<p>Version 0.3.0</p>"
            "<p>Bootstrap ontologies from documents, extract knowledge graphs, "
            "answer multi-hop reasoning questions.</p>",
        )

    # -- Navigation -----------------------------------------------------------

    def _select_view(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_buttons):
            btn.setStyleSheet(NAV_BUTTON_ACTIVE if i == idx else NAV_BUTTON_INACTIVE)
        # Auto-refresh on switch
        if idx == VIEW_DASHBOARD:
            self._refresh_dashboard()
        elif idx == VIEW_GRAPH:
            self._graph_view.refresh()
        elif idx == VIEW_STATUS:
            self._status_view.refresh()
        elif idx == VIEW_VERSIONS:
            self._versions_view.refresh()
        elif idx == VIEW_ACTIVITY:
            self._activity_view.refresh()
        elif idx == VIEW_REVIEW:
            self._review_view.refresh()

    @Slot(str)
    def _navigate_to(self, view_name: str) -> None:
        idx = NAV_MAP.get(view_name, VIEW_DASHBOARD)
        self._select_view(idx)

    # -- Graph selector -------------------------------------------------------

    def _refresh_graph_list(self) -> None:
        self._graph_combo.blockSignals(True)
        self._graph_combo.clear()
        graphs = self._bridge.list_graphs()
        current = self._bridge.current_graph
        if current and current not in graphs:
            graphs.insert(0, current)
        self._graph_combo.addItems(graphs)
        if current in graphs:
            self._graph_combo.setCurrentText(current)
        self._graph_combo.blockSignals(False)

    @Slot(str)
    def _on_graph_changed(self, graph_name: str) -> None:
        if not graph_name:
            return
        self._bridge.switch_graph(graph_name)
        self._refresh_dashboard()
        self._chat_view.new_session()
        self.setWindowTitle(f"SynapseOS — {graph_name}")

    # -- Bootstrap callback ---------------------------------------------------

    @Slot(str)
    def _on_bootstrap_done(self, graph_name: str) -> None:
        """Refresh graph list and switch to the newly bootstrapped graph."""
        self._refresh_graph_list()
        self._graph_combo.setCurrentText(graph_name)
        self._refresh_dashboard()

    # -- Dashboard refresh ----------------------------------------------------

    def _refresh_dashboard(self) -> None:
        try:
            data = self._bridge.get_dashboard_data()
            self._dashboard.update_data(data)
        except Exception as exc:
            logger.error("Dashboard refresh failed: %s", exc)

    # -- Cleanup --------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        self._bridge.close()
        logging.getLogger().removeHandler(self._log_handler)
        super().closeEvent(event)


def main() -> None:
    """Entry point for synapse-gui."""
    import signal

    # Allow Ctrl+C in the terminal to kill the app
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QApplication(sys.argv)
    app.setApplicationName("SynapseOS")
    app.setApplicationVersion("0.3.0")

    apply_theme(app)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
