"""Thread-safe log handler for view-specific log areas.

Uses a QObject signal bridge so log records emitted from worker threads
are safely delivered to Qt widgets on the main thread.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal


class _LogBridge(QObject):
    """Signal bridge: worker thread emits, main thread receives."""
    log_received = Signal(str, str)  # (level, formatted_message)


class SafeViewLogHandler(logging.Handler):
    """A logging.Handler that safely forwards records to the main thread.

    Usage::

        handler = SafeViewLogHandler()
        handler.bridge.log_received.connect(my_slot)
        logging.getLogger("synapse.foo").addHandler(handler)
    """

    def __init__(self, level: int = logging.DEBUG) -> None:
        super().__init__(level)
        self.bridge = _LogBridge()
        self.setFormatter(
            logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.bridge.log_received.emit(record.levelname, msg)
        except Exception:
            self.handleError(record)
