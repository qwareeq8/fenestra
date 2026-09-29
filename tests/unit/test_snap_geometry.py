"""Tests for snap geometry and fullscreen detection (QUAL-03).

Tests calculate_snap_position and _rect_matches_monitor without requiring
Win32 APIs or a running desktop.
"""

import sys
from unittest.mock import patch

import pytest

from fenestra.app.config import DEFAULTS
from fenestra.platform.win32_helpers import FULLSCREEN_TOLERANCE, _rect_matches_monitor
from fenestra.services.snap import calculate_snap_position


def _make_settings(**overrides):
    """Create a minimal settings object from DEFAULTS (avoids conftest import)."""
    obj = type("Settings", (), {})()
    for key, val in DEFAULTS.items():
        setattr(obj, key, overrides.get(key, val))
    obj.save = lambda: None
    obj.clear = lambda: None
    return obj


def test_rect_matches_monitor_exact():
    """Exact match should return True."""
    assert _rect_matches_monitor((0, 0, 1920, 1080), (0, 0, 1920, 1080)) is True


def test_rect_matches_monitor_within_tolerance():
    """Rect within FULLSCREEN_TOLERANCE pixels of monitor edges should match."""
    assert FULLSCREEN_TOLERANCE >= 2  # Sanity-check the constant.
    assert _rect_matches_monitor((-2, -1, 1922, 1081), (0, 0, 1920, 1080)) is True


def test_rect_does_not_match_monitor():
    """Rect clearly not covering the monitor should return False."""
    assert _rect_matches_monitor((100, 100, 800, 600), (0, 0, 1920, 1080)) is False


def test_rect_matches_negative_coords():
    """Second monitor with negative coords should still match."""
    assert _rect_matches_monitor((-1920, 0, 0, 1080), (-1920, 0, 0, 1080)) is True


def test_calculate_snap_position_76pct():
    """76% width/height on 1920x1080 starting at (0,0)."""
    x, y, w, h = calculate_snap_position(0, 0, 1920, 1080, 76, 76)
    assert w == 1920 * 76 // 100  # 1459
    assert h == 1080 * 76 // 100  # 820
    assert x == (1920 - w) // 2  # 230
    assert y == (1080 - h) // 2  # 130


def test_calculate_snap_position_50pct():
    """50% width/height on 1920x1080."""
    x, y, w, h = calculate_snap_position(0, 0, 1920, 1080, 50, 50)
    assert w == 960
    assert h == 540
    assert x == 480
    assert y == 270


def test_calculate_snap_position_100pct():
    """100% should fill the entire monitor."""
    x, y, w, h = calculate_snap_position(0, 0, 1920, 1080, 100, 100)
    assert (x, y, w, h) == (0, 0, 1920, 1080)


def test_calculate_snap_position_offset_monitor():
    """Monitor starting at x=1920 should shift the x position."""
    x, y, w, h = calculate_snap_position(1920, 0, 2560, 1440, 76, 76)
    assert w == 2560 * 76 // 100  # 1945
    assert h == 1440 * 76 // 100  # 1094
    # The x position includes the monitor's left offset.
    assert x == 1920 + (2560 - w) // 2
    assert y == (1440 - h) // 2


def test_calculate_snap_position_negative_coords():
    """Snap geometry on monitor with negative origin (left-of-primary layout)."""
    # The monitor starts at x=-1920 and is 1920x1080 pixels.
    x, y, w, h = calculate_snap_position(-1920, 0, 1920, 1080, 76, 76)
    assert w == 1920 * 76 // 100  # 1459
    assert h == 1080 * 76 // 100  # 820
    # The x position must remain within the negative-coordinate monitor.
    assert x == -1920 + (1920 - w) // 2  # -1690
    assert y == (1080 - h) // 2  # 130


def test_calculate_snap_position_vertical_layout():
    """Snap on monitor below primary (y offset, vertical multi-monitor)."""
    # The monitor starts at y=1080 and is 2560x1440 pixels.
    x, y, w, h = calculate_snap_position(0, 1080, 2560, 1440, 76, 76)
    assert w == 2560 * 76 // 100  # 1945
    assert h == 1440 * 76 // 100  # 1094
    assert x == (2560 - w) // 2  # 307
    assert y == 1080 + (1440 - h) // 2  # 1253


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 APIs only available on Windows")
def test_restore_maximized_window():
    """Restore of a previously-maximized window issues SW_MAXIMIZE (SNAP-04)."""
    import ctypes
    from ctypes import wintypes

    import win32con

    from fenestra.services.snap import SnapRestoreController

    # Bypass initialization to avoid keyboard hooks and window enumeration.
    mgr = SnapRestoreController.__new__(SnapRestoreController)
    mgr.settings = _make_settings()

    test_hwnd = 12345

    # Simulate a window that was maximized before snapping.
    mgr._orig_sizes = {
        test_hwnd: {
            "rect": (100, 100, 800, 600),
            "maximized": True,
        }
    }

    def fake_get_window_rect(hwnd, rect_ptr):
        """Populate rect with a small window (not covering monitor)."""
        rect = ctypes.cast(rect_ptr, ctypes.POINTER(wintypes.RECT)).contents
        rect.left = 200
        rect.top = 200
        rect.right = 600
        rect.bottom = 500

    # No Fenestra window is involved. Patching QApplication itself would
    # delete PySide6's lazily loaded class for every later test.
    with (
        patch("fenestra.services.snap.get_monitor_rect", return_value=(0, 0, 1920, 1080)),
        patch("fenestra.services.snap.USER32") as mock_user32,
        patch("fenestra.services.snap.win32gui.ShowWindow") as mock_show_window,
        patch.object(SnapRestoreController, "_fenestra_window_handles", return_value=set()),
    ):
        mock_user32.GetWindowRect.side_effect = fake_get_window_rect
        mock_user32.IsHungAppWindow.return_value = 0

        mgr._restore(test_hwnd)

    # A previously maximized window must return to its maximized state.
    mock_show_window.assert_called_once_with(test_hwnd, win32con.SW_MAXIMIZE)

    # Restoring consumes the saved window geometry.
    assert test_hwnd not in mgr._orig_sizes
