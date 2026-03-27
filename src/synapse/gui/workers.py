"""Async worker thread and logging bridge for the GUI.

AsyncWorker runs an asyncio coroutine in a dedicated QThread so that the
Qt main thread stays responsive.  Communication back to the UI happens
exclusively through Qt signals (automatically queued across threads).

LogSignalHandler is a logging.Handler that re-emits every log record as a
Qt signal so the log panel can display it in real time.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from typing import Any, Callable, Coroutine

from PySide6.QtCore import QObject, QThread, Signal


# ---------------------------------------------------------------------------
# Signal-only QObject (signals must live on a QObject, not directly on QThread)
# ---------------------------------------------------------------------------

class _WorkerSignals(QObject):
    """Signals emitted by AsyncWorker."""

    progress = Signal(str, float)  # (message, 0.0-1.0 fraction)
    log_record = Signal(str, str)  # (level_name, formatted_message)
    finished = Signal(object)      # result (any picklable object)
    error = Signal(str)            # traceback string


# ---------------------------------------------------------------------------
# AsyncWorker
# ---------------------------------------------------------------------------

class AsyncWorker(QThread):
    """Run an async coroutine in a background thread.

    The worker schedules its own deletion via ``deleteLater()`` after
    the thread finishes, so callers don't need to worry about the
    QThread-must-not-be-destroyed-while-running constraint.

    Usage::

        def make_coro():
            return bootstrap(pdf_files, settings, store)

        worker = AsyncWorker(make_coro)
        worker.signals.finished.connect(self._on_bootstrap_done)
        worker.signals.error.connect(self._on_error)
        worker.start()
    """

    def __init__(
        self,
        coro_factory: Callable[[], Coroutine[Any, Any, Any]],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.signals = _WorkerSignals()
        self._coro_factory = coro_factory
        # Schedule safe cleanup after the thread's event loop returns
        self.finished.connect(self._safe_cleanup)

    # Convenience aliases
    @property
    def progress(self) -> Signal:
        return self.signals.progress

    @property
    def finished_signal(self) -> Signal:
        return self.signals.finished

    @property
    def error_signal(self) -> Signal:
        return self.signals.error

    def run(self) -> None:  # noqa: D401 — Qt override
        """Thread entry point — runs the async coroutine to completion."""
        try:
            result = asyncio.run(self._coro_factory())
            self.signals.finished.emit(result)
        except Exception:
            self.signals.error.emit(traceback.format_exc())

    def _safe_cleanup(self) -> None:
        """Called on the main thread after the worker thread has stopped."""
        self.deleteLater()


# ---------------------------------------------------------------------------
# LogSignalHandler  — bridges Python logging → Qt signal
# ---------------------------------------------------------------------------

class _LogBridge(QObject):
    """QObject that owns the log signal (must be a QObject for signals)."""

    log_record = Signal(str, str)  # (level_name, formatted_message)


class LogSignalHandler(logging.Handler):
    """A logging.Handler that emits each record as a Qt signal.

    Install once at application startup::

        handler = LogSignalHandler()
        handler.bridge.log_record.connect(log_panel.append_log)
        logging.getLogger().addHandler(handler)
    """

    def __init__(self, level: int = logging.DEBUG) -> None:
        super().__init__(level)
        self.bridge = _LogBridge()
        self.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.bridge.log_record.emit(record.levelname, msg)
        except Exception:
            self.handleError(record)
