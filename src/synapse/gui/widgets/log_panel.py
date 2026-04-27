"""Collapsible log panel shown as a QDockWidget at the bottom of the window.

Receives log records from LogSignalHandler and displays them colour-coded
by level.
"""

from __future__ import annotations

from html import escape as _escape

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

LEVEL_COLOURS = {
    "DEBUG": "#8e8ea0",
    "INFO": "#e4e4ed",
    "WARNING": "#f0a030",
    "ERROR": "#ef4444",
    "CRITICAL": "#ef4444",
}

LEVEL_ORDER = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LogPanel(QDockWidget):
    """Dockable log viewer panel."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Logs", parent)
        self.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )

        self._min_level = "INFO"

        # -- Widgets ----------------------------------------------------------
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Menlo", 11))
        self._text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._text.document().setMaximumBlockCount(5000)

        self._level_filter = QComboBox()
        self._level_filter.addItems(LEVEL_ORDER)
        self._level_filter.setCurrentText("INFO")
        self._level_filter.currentTextChanged.connect(self._on_level_changed)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setProperty("secondary", True)
        self._clear_btn.clicked.connect(self._text.clear)

        # -- Layout -----------------------------------------------------------
        toolbar = QHBoxLayout()
        toolbar.addWidget(self._level_filter)
        toolbar.addStretch()
        toolbar.addWidget(self._clear_btn)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addLayout(toolbar)
        layout.addWidget(self._text, stretch=1)

        self.setWidget(container)

    # -- Public slot ----------------------------------------------------------

    @Slot(str, str)
    def append_log(self, level: str, message: str) -> None:
        """Append a formatted log line (called from LogSignalHandler signal)."""
        try:
            if LEVEL_ORDER.index(level) < LEVEL_ORDER.index(self._min_level):
                return
        except ValueError:
            pass  # unknown level — show it
        colour = LEVEL_COLOURS.get(level, "#e4e4ed")
        html = f'<span style="color:{colour}; white-space:pre">{_escape(message)}</span>'
        self._text.append(html)
        # Auto-scroll to bottom
        self._text.moveCursor(QTextCursor.MoveOperation.End)

    # -- Private --------------------------------------------------------------

    @Slot(str)
    def _on_level_changed(self, level: str) -> None:
        self._min_level = level
