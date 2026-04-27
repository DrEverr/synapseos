"""Application theme — modern dark AI-tool aesthetic.

Inspired by ChatGPT, Claude, Cursor — deep navy/charcoal backgrounds,
violet/teal accents, generous spacing, smooth rounded corners.
Call ``apply_theme(app)`` after creating the QApplication.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

# -- Colour tokens -----------------------------------------------------------

BG_DARKEST = QColor("#0d0d12")     # sidebar, deepest layer
BG_DARK = QColor("#111116")        # main background
BG_SURFACE = QColor("#1a1a22")     # cards, panels
BG_ELEVATED = QColor("#222230")    # inputs, hover states
BG_HOVER = QColor("#2a2a3a")       # button hover

FG = QColor("#e4e4ed")             # primary text
FG_SECONDARY = QColor("#8e8ea0")   # secondary text
FG_MUTED = QColor("#55556a")       # disabled, hints

ACCENT = QColor("#7c5cfc")         # primary accent — violet
ACCENT_HOVER = QColor("#9b7dff")   # accent hover
ACCENT_SUBTLE = QColor("#7c5cfc")  # accent at 15% opacity for backgrounds

TEAL = QColor("#2dd4a8")           # success / positive
AMBER = QColor("#f0a030")          # warning
RED = QColor("#ef4444")            # error

BORDER = QColor("#2a2a38")         # subtle borders
BORDER_FOCUS = QColor("#7c5cfc")   # focus ring

# -- QSS stylesheet ----------------------------------------------------------

STYLESHEET = """

/* ── Global ────────────────────────────────────────── */

QMainWindow, QWidget {
    background-color: #111116;
    color: #e4e4ed;
}

/* ── Buttons ───────────────────────────────────────── */

QPushButton {
    background-color: #7c5cfc;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 9px 20px;
    font-weight: 600;
    font-size: 13px;
    letter-spacing: 0.2px;
}

QPushButton:hover {
    background-color: #9b7dff;
}

QPushButton:pressed {
    background-color: #6a4de0;
}

QPushButton:disabled {
    background-color: #2a2a38;
    color: #55556a;
}

QPushButton[secondary="true"] {
    background-color: #222230;
    color: #e4e4ed;
    border: 1px solid #2a2a38;
}

QPushButton[secondary="true"]:hover {
    background-color: #2a2a3a;
    border-color: #7c5cfc;
}

/* ── Text inputs ───────────────────────────────────── */

QLineEdit, QPlainTextEdit, QTextEdit {
    background-color: #1a1a22;
    color: #e4e4ed;
    border: 1px solid #2a2a38;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    selection-background-color: #7c5cfc;
    selection-color: #ffffff;
}

QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border-color: #7c5cfc;
}

QLineEdit::placeholder {
    color: #55556a;
}

/* ── Combo box ─────────────────────────────────────── */

QComboBox {
    background-color: #1a1a22;
    color: #e4e4ed;
    border: 1px solid #2a2a38;
    border-radius: 8px;
    padding: 7px 12px;
    font-size: 13px;
    min-width: 100px;
}

QComboBox:hover {
    border-color: #7c5cfc;
}

QComboBox QAbstractItemView {
    background-color: #1a1a22;
    color: #e4e4ed;
    border: 1px solid #2a2a38;
    border-radius: 6px;
    selection-background-color: #7c5cfc;
    selection-color: #ffffff;
    padding: 4px;
}

QComboBox::drop-down {
    border: none;
    padding-right: 8px;
}

/* ── List widgets ──────────────────────────────────── */

QListWidget {
    background-color: #1a1a22;
    color: #e4e4ed;
    border: 1px solid #2a2a38;
    border-radius: 8px;
    padding: 4px;
    font-size: 13px;
    outline: none;
}

QListWidget::item {
    padding: 8px 12px;
    border-radius: 6px;
    margin: 1px 2px;
}

QListWidget::item:hover {
    background-color: #222230;
}

QListWidget::item:selected {
    background-color: #7c5cfc;
    color: #ffffff;
}

/* ── Tables ────────────────────────────────────────── */

QTableWidget {
    background-color: #1a1a22;
    color: #e4e4ed;
    border: 1px solid #2a2a38;
    border-radius: 8px;
    gridline-color: #222230;
    font-size: 13px;
    outline: none;
}

QTableWidget::item {
    padding: 6px 10px;
}

QTableWidget::item:selected {
    background-color: #7c5cfc;
    color: #ffffff;
}

QHeaderView::section {
    background-color: #1a1a22;
    color: #8e8ea0;
    border: none;
    border-bottom: 1px solid #2a2a38;
    padding: 8px 10px;
    font-weight: 600;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

/* ── Tabs ──────────────────────────────────────────── */

QTabWidget::pane {
    border: 1px solid #2a2a38;
    border-radius: 8px;
    background-color: #111116;
    top: -1px;
}

QTabBar {
    background-color: #0d0d12;
}

QTabBar::tab {
    background-color: #1a1a22;
    color: #8e8ea0;
    border: 1px solid #2a2a38;
    border-bottom: none;
    border-radius: 8px 8px 0px 0px;
    padding: 10px 22px;
    font-size: 13px;
    font-weight: 500;
    margin-right: 2px;
    min-width: 80px;
}

QTabBar::tab:hover {
    color: #e4e4ed;
    background-color: #222230;
}

QTabBar::tab:selected {
    color: #7c5cfc;
    background-color: #111116;
    border-bottom: 2px solid #7c5cfc;
    font-weight: 600;
}

/* ── Progress bars ─────────────────────────────────── */

QProgressBar {
    background-color: #222230;
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
    color: transparent;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #7c5cfc, stop:1 #2dd4a8);
    border-radius: 4px;
}

/* ── Checkboxes ────────────────────────────────────── */

QCheckBox {
    color: #e4e4ed;
    font-size: 13px;
    spacing: 8px;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border: 2px solid #2a2a38;
    border-radius: 5px;
    background-color: #1a1a22;
}

QCheckBox::indicator:hover {
    border-color: #7c5cfc;
}

QCheckBox::indicator:checked {
    background-color: #7c5cfc;
    border-color: #7c5cfc;
}

/* ── Group boxes ───────────────────────────────────── */

QGroupBox {
    color: #e4e4ed;
    border: 1px solid #2a2a38;
    border-radius: 10px;
    margin-top: 14px;
    padding-top: 18px;
    font-weight: 600;
    font-size: 13px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 10px;
    color: #8e8ea0;
}

/* ── Scrollbars ────────────────────────────────────── */

QScrollBar:vertical {
    background-color: transparent;
    width: 8px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background-color: #2a2a38;
    border-radius: 4px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background-color: #55556a;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    background-color: transparent;
    height: 8px;
}

QScrollBar::handle:horizontal {
    background-color: #2a2a38;
    border-radius: 4px;
    min-width: 30px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #55556a;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

/* ── Dock widgets ──────────────────────────────────── */

QDockWidget {
    color: #e4e4ed;
    font-size: 12px;
}

QDockWidget::title {
    background-color: #0d0d12;
    border: 1px solid #2a2a38;
    padding: 6px 10px;
    text-align: left;
}

/* ── Splitters ─────────────────────────────────────── */

QSplitter::handle {
    background-color: #2a2a38;
}

QSplitter::handle:horizontal {
    width: 1px;
}

QSplitter::handle:vertical {
    height: 1px;
}

/* ── Trees ─────────────────────────────────────────── */

QTreeWidget {
    background-color: #1a1a22;
    color: #e4e4ed;
    border: 1px solid #2a2a38;
    border-radius: 8px;
    font-size: 13px;
    outline: none;
}

QTreeWidget::item {
    padding: 4px 6px;
    border-radius: 4px;
}

QTreeWidget::item:hover {
    background-color: #222230;
}

QTreeWidget::item:selected {
    background-color: #7c5cfc;
    color: #ffffff;
}

/* ── Tooltips ──────────────────────────────────────── */

QToolTip {
    background-color: #222230;
    color: #e4e4ed;
    border: 1px solid #2a2a38;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}

/* ── Message boxes ─────────────────────────────────── */

QMessageBox {
    background-color: #111116;
}

/* ── Labels ────────────────────────────────────────── */

QLabel {
    color: #e4e4ed;
}

/* ── Spin boxes ────────────────────────────────────── */

QSpinBox {
    background-color: #1a1a22;
    color: #e4e4ed;
    border: 1px solid #2a2a38;
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 13px;
}

QSpinBox:focus {
    border-color: #7c5cfc;
}
"""


def apply_theme(app: QApplication) -> None:
    """Apply the modern dark AI-tool theme to the application."""
    palette = QPalette()

    palette.setColor(QPalette.ColorRole.Window, BG_DARK)
    palette.setColor(QPalette.ColorRole.WindowText, FG)
    palette.setColor(QPalette.ColorRole.Base, BG_SURFACE)
    palette.setColor(QPalette.ColorRole.AlternateBase, BG_ELEVATED)
    palette.setColor(QPalette.ColorRole.Text, FG)
    palette.setColor(QPalette.ColorRole.PlaceholderText, FG_MUTED)
    palette.setColor(QPalette.ColorRole.Button, BG_ELEVATED)
    palette.setColor(QPalette.ColorRole.ButtonText, FG)
    palette.setColor(QPalette.ColorRole.Highlight, ACCENT)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, ACCENT)
    palette.setColor(QPalette.ColorRole.LinkVisited, ACCENT_HOVER)
    palette.setColor(QPalette.ColorRole.ToolTipBase, BG_ELEVATED)
    palette.setColor(QPalette.ColorRole.ToolTipText, FG)

    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, FG_MUTED)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, FG_MUTED)

    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)

    import platform

    system = platform.system()
    if system == "Darwin":
        family = ".AppleSystemUIFont"
    elif system == "Windows":
        family = "Segoe UI"
    else:
        family = "sans-serif"
    font = QFont(family, 13)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)
