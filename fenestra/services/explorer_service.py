"""Explorer column auto-size service.

The ``ex_auto_size`` setting gates the ``ExplorerAutosizeWorker`` lifecycle.
COM operations remain in ``workers/explorer.py`` under the Phase 3 D-07
constraint.
"""

import logging

from PySide6 import QtCore

from fenestra.platform.win32_helpers import _is_window_interactive
from fenestra.workers.explorer import ExplorerAutosizeWorker

LOG = logging.getLogger("Fenestra")


def _autosize_explorer_columns(
    top_hwnd: int,
    target_index: int,
    target_path: str | None = None,
    dump_columns: bool = False,
) -> tuple[bool, str]:
    """Run one COM autosize attempt, with column diagnostics when requested.

    ``ExplorerAutosizeWorker`` calls this from the thread that owns its COM
    apartment.
    """
    # Keep the Windows COM implementation lazy so lifecycle tests and Linux
    # test collection do not require comtypes.
    from fenestra.services.explorer_columns import autosize_explorer_columns

    return autosize_explorer_columns(
        top_hwnd,
        target_index,
        target_path=target_path,
        dump_columns=dump_columns,
    )


class ExplorerService:
    """Lifecycle manager for the Explorer column auto-size worker.

    Follows the same facade pattern as SnapService. MainWindow creates it,
    passes settings, and delegates start/stop to it.
    """

    def __init__(self, settings, parent=None):
        """Accept settings and optional QObject parent for thread parenting."""
        self._settings = settings
        self._parent = parent
        self._thread = None
        self._worker = None
        self._stop_requested = False
        self._start_when_stopped = False
        self._restart_attempts = 0
        self._restart_scheduled = False

    _MAX_RESTART_ATTEMPTS = 3

    def start(self) -> None:
        """Start the Explorer worker if ``ex_auto_size`` is enabled.

        If the setting is disabled, stops any running worker instead.
        If a worker is already running, this is a no-op.
        """
        if not bool(self._settings.ex_auto_size):
            if self.is_running():
                LOG.info("Explorer autosize: stopping (disabled).")
                self.stop()
            return

        if self._thread and self._thread.isRunning():
            if self._stop_requested:
                self._start_when_stopped = True
            return

        self._start_when_stopped = False
        self._restart_attempts = 0
        self._start_worker()

    def _start_worker(self) -> None:
        """Create one worker without resetting the crash-recovery budget."""
        if self._thread and self._thread.isRunning():
            return

        self._stop_requested = False
        self._thread = QtCore.QThread(self._parent)
        self._worker = ExplorerAutosizeWorker(_autosize_explorer_columns, _is_window_interactive)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        thread = self._thread
        self._thread.finished.connect(lambda: self._on_finished(thread))
        self._thread.start()
        LOG.info("Explorer autosize: worker started with the tab-aware engine.")

    def stop(self) -> None:
        """Stop the explorer worker cleanly."""
        self._stop_requested = True
        self._restart_scheduled = False
        self._start_when_stopped = False
        self._restart_attempts = 0
        worker = self._worker
        thread = self._thread
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                pass
        if thread is not None:
            thread.quit()
            if not thread.wait(3000):
                # The worker is blocked in a COM call. Do not drop the
                # references: clearing them would let a later start() spawn a
                # second worker while this thread is still alive, and the
                # parented QThread could be destroyed while running. Leave them
                # so is_running() stays True and start() no-ops; _on_finished
                # clears them when the thread actually exits.
                LOG.warning(
                    "Explorer autosize: the thread did not stop in time; keeping its reference."
                )
                return
        self._worker = None
        self._thread = None
        LOG.info("Explorer autosize: worker stopped.")

    def is_running(self) -> bool:
        """Return True if the explorer worker thread is currently running."""
        return self._thread is not None and self._thread.isRunning()

    def _on_finished(self, finished_thread=None) -> None:
        """Release the finished worker thread and its worker.

        Only clears references when the finishing thread is the current one,
        so a stale queued signal cannot null out a newer worker.
        """
        if finished_thread is not None and finished_thread is not self._thread:
            return
        if self._thread is not None and self._thread.isRunning():
            return
        expected = self._stop_requested
        restart_after_stop = self._start_when_stopped and bool(self._settings.ex_auto_size)
        self._worker = None
        self._thread = None
        if restart_after_stop:
            self._start_when_stopped = False
            self._stop_requested = False
            self._restart_attempts = 0
            QtCore.QTimer.singleShot(0, self._start_worker)
            return
        if expected or not bool(self._settings.ex_auto_size):
            return
        self._restart_attempts += 1
        if self._restart_attempts > self._MAX_RESTART_ATTEMPTS:
            LOG.error(
                "Explorer autosize: worker stopped unexpectedly too many times; "
                "automatic recovery is disabled."
            )
            return
        delay_ms = min(1000 * (2 ** (self._restart_attempts - 1)), 8000)
        self._restart_scheduled = True
        LOG.warning(
            "Explorer autosize: worker stopped unexpectedly; restart %d/%d in %.1f seconds.",
            self._restart_attempts,
            self._MAX_RESTART_ATTEMPTS,
            delay_ms / 1000.0,
        )
        QtCore.QTimer.singleShot(delay_ms, self._restart_after_failure)

    def _restart_after_failure(self) -> None:
        """Restart after an unexpected exit if the feature is still enabled."""
        if not self._restart_scheduled:
            return
        self._restart_scheduled = False
        if self._stop_requested or not bool(self._settings.ex_auto_size):
            return
        self._start_worker()
