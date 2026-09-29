"""Thread-safe ownership of the global key-capture hook."""

import threading


class CaptureGuard:
    """Coordinate exclusive ownership of the global key-capture hook."""

    def __init__(self):
        self._lock = threading.Lock()
        self._active = False

    def try_start(self) -> bool:
        """Acquire capture ownership if no capture is already active."""
        with self._lock:
            if self._active:
                return False
            self._active = True
            return True

    def finish(self) -> None:
        """Release capture ownership."""
        with self._lock:
            self._active = False

    @property
    def is_active(self) -> bool:
        """Return whether a key-capture session currently owns the guard."""
        with self._lock:
            return self._active
