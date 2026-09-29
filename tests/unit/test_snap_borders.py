"""Tests for DWM invisible-border compensation in snap centering."""

from fenestra.services.snap import window_border_deltas


def test_no_visible_rect_means_zero_borders():
    assert window_border_deltas((0, 0, 100, 100), None) == (0, 0, 0, 0)


def test_typical_win11_borders():
    """A typical window has ~7px invisible borders on the sides and bottom."""
    win = (100, 100, 900, 700)
    visible = (107, 100, 893, 693)
    assert window_border_deltas(win, visible) == (7, 0, 7, 7)


def test_identical_rects_mean_zero_borders():
    rect = (10, 20, 300, 400)
    assert window_border_deltas(rect, rect) == (0, 0, 0, 0)


def test_bogus_dwm_answer_is_clamped():
    """A DWM rect wildly outside the window rect must not fling the window."""
    win = (0, 0, 100, 100)
    visible = (500, -500, -500, 900)
    left, top, right, bottom = window_border_deltas(win, visible)
    assert 0 <= left <= 64
    assert 0 <= top <= 64
    assert 0 <= right <= 64
    assert 0 <= bottom <= 64


def test_fullscreen_detection_compares_the_visible_frame_with_the_monitor(monkeypatch):
    """A frame within the tolerance of the monitor bounds counts as fullscreen."""
    from fenestra.platform import win32_helpers

    monitor = (0, 0, 1920, 1080)
    monkeypatch.setattr(win32_helpers.win32gui, "IsWindow", lambda _hwnd: True)

    monkeypatch.setattr(win32_helpers, "_get_window_dwm_rect", lambda _hwnd: (-2, 1, 1921, 1080))
    assert win32_helpers._is_window_fullscreen(42, monitor_rect=monitor)

    monkeypatch.setattr(win32_helpers, "_get_window_dwm_rect", lambda _hwnd: (0, 0, 1600, 900))
    assert not win32_helpers._is_window_fullscreen(42, monitor_rect=monitor)

    monkeypatch.setattr(win32_helpers, "_get_window_dwm_rect", lambda _hwnd: None)
    assert not win32_helpers._is_window_fullscreen(42, monitor_rect=monitor)


def test_exit_fullscreen_restores_a_maximized_window(monkeypatch):
    """A maximized window leaves that state before it is moved."""
    from fenestra.platform import win32_helpers

    shown = []
    monkeypatch.setattr(
        win32_helpers.win32gui,
        "GetWindowPlacement",
        lambda _hwnd: (0, win32_helpers.win32con.SW_SHOWMAXIMIZED, (0, 0), (0, 0), (0, 0, 0, 0)),
    )
    monkeypatch.setattr(
        win32_helpers.win32gui, "ShowWindow", lambda hwnd, command: shown.append(command)
    )

    win32_helpers._exit_fullscreen(42)

    assert shown == [win32_helpers.win32con.SW_RESTORE]
