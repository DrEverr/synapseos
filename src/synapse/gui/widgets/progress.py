"""Multi-step progress indicator.

Displays a horizontal list of step labels with visual states:
pending, active (with spinner feel), and completed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

STYLE_PENDING = "color: #55556a; font-size: 12px;"
STYLE_ACTIVE = "color: #7c5cfc; font-weight: bold; font-size: 12px;"
STYLE_DONE = "color: #2dd4a8; font-size: 12px;"


class StepProgress(QWidget):
    """A horizontal stepper showing named steps + a progress bar."""

    def __init__(self, steps: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._step_names = steps
        self._current = -1

        # Step labels
        self._step_labels: list[QLabel] = []
        steps_row = QHBoxLayout()
        steps_row.setSpacing(4)
        for i, name in enumerate(steps):
            sep = QLabel("  >  ") if i > 0 else None
            if sep:
                sep.setStyleSheet("color: #55556a; font-size: 12px;")
                steps_row.addWidget(sep)
            lbl = QLabel(name)
            lbl.setStyleSheet(STYLE_PENDING)
            steps_row.addWidget(lbl)
            self._step_labels.append(lbl)
        steps_row.addStretch()

        # Progress bar
        self._bar = QProgressBar()
        self._bar.setRange(0, len(steps))
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(steps_row)
        layout.addWidget(self._bar)

    def set_step(self, index: int) -> None:
        """Activate the step at *index* (0-based). Previous steps become 'done'."""
        self._current = index
        for i, lbl in enumerate(self._step_labels):
            if i < index:
                lbl.setStyleSheet(STYLE_DONE)
                lbl.setText(f"  {self._step_names[i]}")
            elif i == index:
                lbl.setStyleSheet(STYLE_ACTIVE)
                lbl.setText(f"  {self._step_names[i]}...")
            else:
                lbl.setStyleSheet(STYLE_PENDING)
                lbl.setText(self._step_names[i])
        self._bar.setValue(index)

    def complete(self) -> None:
        """Mark all steps as done."""
        for i, lbl in enumerate(self._step_labels):
            lbl.setStyleSheet(STYLE_DONE)
            lbl.setText(f"  {self._step_names[i]}")
        self._bar.setValue(len(self._step_names))

    def reset(self) -> None:
        """Reset all steps to pending."""
        self._current = -1
        for i, lbl in enumerate(self._step_labels):
            lbl.setStyleSheet(STYLE_PENDING)
            lbl.setText(self._step_names[i])
        self._bar.setValue(0)
