"""Focused tests for MainWindow capture, shortcut, and shutdown ownership."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fenestra.bridge.capture_guard import CaptureGuard


class _CaptureThread:
    def __init__(self, *, stops_in_time: bool) -> None:
        self.stops_in_time = stops_in_time
        self.running = True
        self.quit = MagicMock()

    def wait(self, timeout: int) -> bool:
        assert timeout == 2000
        return self.stops_in_time

    def isRunning(self) -> bool:
        return self.running


@pytest.mark.requires_qt
def test_capture_timeout_keeps_thread_ownership_until_finished() -> None:
    """A timed-out hook thread keeps its references and exclusive capture guard."""
    from fenestra.app.window import MainWindow

    guard = CaptureGuard()
    assert guard.try_start()
    thread = _CaptureThread(stops_in_time=False)
    worker = SimpleNamespace(stop=MagicMock())
    shortcut = SimpleNamespace(setEnabled=MagicMock())
    window = SimpleNamespace(
        _capture_guard=guard,
        _capture_thread=thread,
        _capture_worker=worker,
        _capture_target="snap",
        _frontend_shortcuts_enabled=True,
        _window_shortcuts=[shortcut],
    )
    window._refresh_window_shortcuts = lambda: MainWindow._refresh_window_shortcuts(window)
    window._on_capture_finished = lambda finished=None: MainWindow._on_capture_finished(
        window, finished
    )

    MainWindow._stop_capture_worker(window)

    assert window._capture_thread is thread
    assert window._capture_worker is worker
    assert guard.is_active

    thread.running = False
    MainWindow._on_capture_finished(window, thread)

    assert window._capture_thread is None
    assert window._capture_worker is None
    assert not guard.is_active
    shortcut.setEnabled.assert_called_with(True)


@pytest.mark.requires_qt
def test_native_shortcuts_are_disabled_during_key_capture() -> None:
    """Qt shortcuts cannot consume Ctrl+Enter while the global capture hook owns input."""
    from fenestra.app.window import MainWindow

    guard = CaptureGuard()
    assert guard.try_start()
    shortcut = SimpleNamespace(setEnabled=MagicMock())
    window = SimpleNamespace(
        _capture_guard=guard,
        _frontend_shortcuts_enabled=True,
        _window_shortcuts=[shortcut],
    )

    MainWindow._refresh_window_shortcuts(window)

    shortcut.setEnabled.assert_called_once_with(False)


@pytest.mark.requires_qt
def test_background_shutdown_is_idempotent() -> None:
    """Repeated Qt and atexit cleanup paths perform blocking waits only once."""
    from fenestra.app.window import MainWindow

    window = SimpleNamespace(
        _shutdown_started=False,
        _bridge=SimpleNamespace(wait_for_view_task=MagicMock()),
        _stop_capture_worker=MagicMock(),
        _explorer_service=SimpleNamespace(stop=MagicMock()),
    )

    MainWindow._stop_background_threads(window)
    MainWindow._stop_background_threads(window)

    window._bridge.wait_for_view_task.assert_called_once_with()
    window._stop_capture_worker.assert_called_once_with()
    window._explorer_service.stop.assert_called_once_with()


@pytest.mark.requires_qt
def test_native_test_snap_uses_explicit_self_test_path() -> None:
    """Ctrl+Enter uses the same self-safe test service as the frontend button."""
    from fenestra.app.window import MainWindow

    guard = CaptureGuard()
    service = SimpleNamespace(
        test_snap=MagicMock(
            return_value={
                "ok": True,
                "message": "Snapped the window behind Fenestra.",
            }
        )
    )
    bridge = SimpleNamespace(snap_status=SimpleNamespace(emit=MagicMock()))
    window = SimpleNamespace(_capture_guard=guard, _snap_service=service, _bridge=bridge)

    MainWindow._test_snap(window)

    service.test_snap.assert_called_once_with()
    bridge.snap_status.emit.assert_called_once_with(
        "Snapped the window behind Fenestra.", 2000, "success"
    )


@pytest.mark.requires_qt
def test_activation_message_restores_the_window_through_qt(monkeypatch) -> None:
    """The instance answers a second launch by restoring its own window later."""
    import ctypes
    from ctypes import wintypes

    from fenestra.app import window as window_module

    single_shot = MagicMock()
    monkeypatch.setattr(
        window_module, "QtCore", SimpleNamespace(QTimer=SimpleNamespace(singleShot=single_shot))
    )
    window = SimpleNamespace(_activate_message=0xC123, _restore_window=MagicMock())
    message = wintypes.MSG()
    message.message = 0xC123

    handled = window_module.MainWindow.nativeEvent(
        window, b"windows_generic_MSG", ctypes.addressof(message)
    )

    assert handled == (True, 0)
    single_shot.assert_called_once_with(0, window._restore_window)
    window._restore_window.assert_not_called()


@pytest.mark.requires_qt
def test_title_bar_hit_test_reads_the_native_message_window(monkeypatch) -> None:
    """A press on the empty title bar is a caption hit that drags the window."""
    import ctypes
    from ctypes import wintypes

    from fenestra.app import window as window_module

    queried = []

    def get_window_rect(hwnd, rect_pointer):
        queried.append(hwnd)
        rect = ctypes.cast(rect_pointer, ctypes.POINTER(wintypes.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = 100, 50, 1100, 670
        return True

    monkeypatch.setattr(window_module, "USER32", SimpleNamespace(GetWindowRect=get_window_rect))
    window = SimpleNamespace(
        _activate_message=None,
        _hit_test_interactive_width=320,
        _hit_test_controls_width=72,
        _hit_test_title_bar_height=34,
        devicePixelRatioF=lambda: 1.0,
    )
    message = wintypes.MSG()
    message.hWnd = 0x1234
    message.message = 0x0084  # WM_NCHITTEST
    message.lParam = (65 << 16) | 700  # Screen point (700, 65), inside the spacer.

    handled = window_module.MainWindow.nativeEvent(
        window, b"windows_generic_MSG", ctypes.addressof(message)
    )

    assert handled == (True, 2)  # HTCAPTION
    assert queried == [0x1234]


@pytest.mark.requires_qt
@pytest.mark.parametrize(
    ("message_id", "w_param"),
    [(0x00A3, 2), (0x0112, 0xF030), (0x0112, 0xF032)],
)
def test_the_fixed_size_window_refuses_to_maximize(message_id, w_param) -> None:
    """A title-bar double-click, Win+Up, and the system menu cannot maximize."""
    import ctypes
    from ctypes import wintypes

    from fenestra.app import window as window_module

    window = SimpleNamespace(_activate_message=None)
    message = wintypes.MSG()
    message.message = message_id
    message.wParam = w_param

    handled = window_module.MainWindow.nativeEvent(
        window, b"windows_generic_MSG", ctypes.addressof(message)
    )

    assert handled == (True, 0)


@pytest.mark.requires_qt
def test_a_scale_change_on_the_same_monitor_centers_the_window_once_settled() -> None:
    """A new scale re-centers the window, but dragging never does."""
    from fenestra.app.window import MainWindow

    primary, secondary = object(), object()  # Two monitors.
    state = {"screen": primary, "ratio": 1.0}
    window = SimpleNamespace(
        _scale_key=None,
        _in_move_loop=False,
        _recenter_pending=False,
        _recenter_timer=SimpleNamespace(start=MagicMock()),
        windowHandle=lambda: SimpleNamespace(screen=lambda: state["screen"]),
        devicePixelRatioF=lambda: state["ratio"],
        _center_on_current_screen=MagicMock(),
    )
    track = lambda: MainWindow._track_scale(window)  # noqa: E731

    track()  # The first measurement only sets the baseline.
    state["ratio"] = 1.5
    window._in_move_loop = True
    track()  # A ratio change during a drag is the user moving the window.
    window._in_move_loop = False
    state["screen"], state["ratio"] = secondary, 1.25
    track()  # A move to another monitor keeps the window where it was dropped.
    assert window._recenter_pending is False
    window._recenter_timer.start.assert_not_called()

    state["ratio"] = 2.0
    track()  # The display scale of the same monitor changed.
    track()  # Windows then repositions the window, which restarts the delay.
    assert window._recenter_timer.start.call_count == 2
    window._center_on_current_screen.assert_not_called()

    MainWindow._finish_pending_recenter(window)
    MainWindow._finish_pending_recenter(window)
    window._center_on_current_screen.assert_called_once_with()


@pytest.mark.requires_qt
def test_the_window_centers_on_the_monitor_it_is_on() -> None:
    """A snap gesture over Fenestra centers it on its current monitor."""
    from fenestra.app.window import MainWindow

    screen = object()
    window = SimpleNamespace(
        windowHandle=lambda: SimpleNamespace(screen=lambda: screen),
        center_on_screen=MagicMock(),
    )

    MainWindow._center_on_current_screen(window)

    window.center_on_screen.assert_called_once_with(screen)
