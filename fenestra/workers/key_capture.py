"""Key capture session and worker.

KeyCaptureSession is pure Python (no PySide6 dependency).
KeyCaptureWorker wraps it in a QObject for use on a QThread.
"""

import logging
import threading

LOG = logging.getLogger("Fenestra")


class KeyCaptureSession:
    """Capture one global key press with cancellation and timeout support."""

    def __init__(self, keyboard, cancel_key="esc", timeout_s=15.0):
        self._keyboard = keyboard
        self._cancel_key = cancel_key
        self._timeout_s = timeout_s
        self._done = threading.Event()
        self._stop = threading.Event()
        self._result = None
        self._reason = None

    def stop(self) -> None:
        """Stop the session and wake its wait loop."""
        if self._reason is None:
            self._reason = "stopped"
        self._stop.set()
        self._done.set()

    def run(self):
        """Run the capture session and return the captured key and stop reason."""
        self._result = None
        self._reason = None
        # Honor a stop requested before the session actually started running
        # (for example, a quit issued right after capture began). Do not clear
        # the stop/done events here or that request would be lost, leaving a
        # global keyboard hook installed after teardown.
        if self._stop.is_set():
            self._reason = "stopped"
            return self._result, self._reason
        hook_id = None
        try:
            hook_id = self._keyboard.hook(self._on_event)
            # A captured key, Esc, and stop() all set the done event, so the
            # wait ends at once for each of them. Only a timeout returns False.
            if not self._done.wait(self._timeout_s):
                self._reason = "timeout"
        except Exception:
            LOG.exception("The key capture hook failed.")
            self._reason = "error"
        finally:
            if hook_id is not None:
                try:
                    self._keyboard.unhook(hook_id)
                except Exception:
                    pass
        if self._reason is None:
            self._reason = "captured" if self._result else "stopped"
        return self._result, self._reason

    def _on_event(self, event):
        if self._done.is_set():
            return
        event_type = getattr(event, "event_type", "") or ""
        if event_type and event_type != "down":
            return
        name = getattr(event, "name", "") or ""
        key = str(name).lower()
        if not key:
            return
        if key == self._cancel_key:
            self._reason = "esc"
            self._done.set()
            return
        self._result = key
        self._reason = "captured"
        self._done.set()


# Import PySide6 conditionally for CI compatibility under D-10.
try:
    from PySide6 import QtCore
except ImportError:  # pragma: no cover - PySide6 is unavailable in some test environments.
    QtCore = None


if QtCore is not None:

    class KeyCaptureWorker(QtCore.QObject):
        """Expose a key-capture session through Qt signals for a worker thread."""

        captured = QtCore.Signal(str)
        cancelled = QtCore.Signal(str)
        finished = QtCore.Signal()

        def __init__(self, keyboard_module=None, cancel_key="esc", timeout_s=15.0):
            super().__init__()
            if keyboard_module is None:
                import keyboard as keyboard_module
            self._session = KeyCaptureSession(
                keyboard_module,
                cancel_key=cancel_key,
                timeout_s=timeout_s,
            )

        @QtCore.Slot()
        def run(self):
            """Run the session and always emit ``finished`` during teardown."""
            # The ``finished`` signal must always emit, or the CaptureGuard stays
            # locked and the capture QThread never quits.
            try:
                key, reason = self._session.run()
                if key:
                    self.captured.emit(key)
                else:
                    self.cancelled.emit(reason)
            except Exception:
                LOG.exception("The key capture session crashed.")
                self.cancelled.emit("error")
            finally:
                self.finished.emit()

        def stop(self) -> None:
            """Request that the capture session stop."""
            self._session.stop()
