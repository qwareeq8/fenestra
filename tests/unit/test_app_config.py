"""Tests for app_config defaults and normalization (QUAL-03 supporting)."""

from fenestra.app.config import APP_NAME, APP_VERSION, DEFAULTS, normalize_snap_presses


def test_defaults_has_all_keys():
    """DEFAULTS should contain all 14 settings keys."""
    expected_keys = {
        "snap_key",
        "restore_key",
        "enable_snap",
        "snap_presses",
        "snap_interval",
        "width_pct",
        "height_pct",
        "ex_auto_size",
        "game_mode_enabled",
        "run_at_startup",
        "theme",
        "accent",
        "density",
        "minimize_to_tray",
    }
    assert set(DEFAULTS.keys()) == expected_keys


def test_normalize_snap_presses_valid():
    """Valid integers should pass through (clamped to min 1)."""
    assert normalize_snap_presses(3) == 3
    assert normalize_snap_presses(1) == 1


def test_normalize_snap_presses_string():
    """String number should be coerced to int."""
    assert normalize_snap_presses("5") == 5


def test_normalize_snap_presses_zero_clamps():
    """Zero should clamp to minimum of 1."""
    assert normalize_snap_presses(0) == 1


def test_normalize_snap_presses_invalid():
    """Non-numeric input should return the default value."""
    assert normalize_snap_presses("abc") == DEFAULTS["snap_presses"]


def test_app_name_is_fenestra():
    """APP_NAME must be 'Fenestra' (not the old product name)."""
    assert APP_NAME == "Fenestra"


def test_app_version_format():
    """APP_VERSION should be a non-empty string in semver format."""
    assert isinstance(APP_VERSION, str)
    assert len(APP_VERSION) > 0
    parts = APP_VERSION.split(".")
    assert len(parts) == 3
