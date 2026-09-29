"""Tests for settings bounds clamping on the registry read path."""

from fenestra.app.config import DEFAULTS, normalize_snap_presses
from fenestra.settings.persistence import _safe_int


def test_safe_int_clamps_low():
    """A width percentage of 0 from a corrupted registry clamps to the floor."""
    assert _safe_int(0, DEFAULTS["width_pct"], bounds=(10, 100)) == 10


def test_safe_int_clamps_high():
    """A width percentage above 100 clamps to the ceiling instead of going off-screen."""
    assert _safe_int(150, DEFAULTS["width_pct"], bounds=(10, 100)) == 100


def test_safe_int_in_range_passes_through():
    assert _safe_int(76, 50, bounds=(10, 100)) == 76


def test_safe_int_garbage_falls_back_to_default():
    assert _safe_int("garbage", 76, bounds=(10, 100)) == 76


def test_safe_int_none_falls_back_to_default():
    assert _safe_int(None, 76, bounds=(10, 100)) == 76


def test_normalize_snap_presses_clamps_upper():
    """A huge press count would make the trigger unreachable; clamp to 10."""
    assert normalize_snap_presses(10_000) == 10


def test_normalize_snap_presses_clamps_lower():
    assert normalize_snap_presses(0) == 1


def test_normalize_snap_presses_garbage_uses_default():
    assert normalize_snap_presses("abc") == DEFAULTS["snap_presses"]
