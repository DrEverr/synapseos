"""Reusable document file picker with drag-and-drop support.

Provides a drop zone + browse button and emits ``files_changed`` whenever
the list of selected files changes. Supports PDF, Markdown, text, HTML, email.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class FilePicker(QWidget):
    """Widget for selecting document files via browse dialog or drag-and-drop."""

    files_changed = Signal(list)  # list[str] — absolute paths

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._files: list[str] = []

        # -- Drop zone label --------------------------------------------------
        self._drop_label = QLabel("Drop documents here\nor click Browse")
        self._drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drop_label.setMinimumHeight(80)
        self._drop_label.setStyleSheet("""
            QLabel {
                border: 2px dashed #2a2a38;
                border-radius: 8px;
                color: #8e8ea0;
                font-size: 14px;
                padding: 16px;
                background-color: #1a1a22;
            }
        """)

        # -- Buttons ----------------------------------------------------------
        self._browse_btn = QPushButton("Browse")
        self._browse_btn.clicked.connect(self._browse)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setProperty("secondary", True)
        self._clear_btn.clicked.connect(self._clear)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._browse_btn)
        btn_row.addWidget(self._clear_btn)
        btn_row.addStretch()

        # -- File list --------------------------------------------------------
        self._file_list = QListWidget()
        self._file_list.setMaximumHeight(150)

        # -- Layout -----------------------------------------------------------
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._drop_label)
        layout.addLayout(btn_row)
        layout.addWidget(self._file_list)

    # -- Public API -----------------------------------------------------------

    @property
    def files(self) -> list[str]:
        return list(self._files)

    # -- Drag & drop ----------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._drop_label.setStyleSheet(self._drop_label.styleSheet().replace(
                "border: 2px dashed #2a2a38",
                "border: 2px dashed #7c5cfc",
            ))

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._drop_label.setStyleSheet(self._drop_label.styleSheet().replace(
            "border: 2px dashed #7c5cfc",
            "border: 2px dashed #2a2a38",
        ))

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        self._drop_label.setStyleSheet(self._drop_label.styleSheet().replace(
            "border: 2px dashed #7c5cfc",
            "border: 2px dashed #2a2a38",
        ))
        urls = event.mimeData().urls()
        new_files: list[str] = []
        for url in urls:
            path = url.toLocalFile()
            if not path:
                continue
            p = Path(path)
            if p.is_dir():
                from synapse.parsers import SUPPORTED_EXTENSIONS
                for ext in SUPPORTED_EXTENSIONS:
                    new_files.extend(str(f) for f in sorted(p.glob(f"*{ext}")))
            elif p.is_file():
                from synapse.parsers import is_supported
                if is_supported(str(p)):
                    new_files.append(str(p))
        self._add_files(new_files)

    # -- Browse dialog --------------------------------------------------------

    def _browse(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select documents",
            "",
            "Documents (*.pdf *.md *.txt *.html *.htm *.eml);;PDF (*.pdf);;Markdown (*.md);;Text (*.txt);;HTML (*.html *.htm);;Email (*.eml);;All Files (*)",
        )
        if files:
            self._add_files(files)

    # -- Internal -------------------------------------------------------------

    def _add_files(self, paths: list[str]) -> None:
        seen = set(self._files)
        for p in paths:
            resolved = str(Path(p).resolve())
            if resolved not in seen:
                seen.add(resolved)
                self._files.append(resolved)
        self._refresh_list()
        self.files_changed.emit(self._files)

    def _clear(self) -> None:
        self._files.clear()
        self._refresh_list()
        self.files_changed.emit(self._files)

    def _refresh_list(self) -> None:
        self._file_list.clear()
        for f in self._files:
            name = Path(f).name
            self._file_list.addItem(QListWidgetItem(name))
        self._drop_label.setText(
            f"{len(self._files)} file(s) selected"
            if self._files
            else "Drop documents here\nor click Browse"
        )
