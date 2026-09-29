"""Tests for SettingsState validation, draft model, and coercion (QUAL-03, STRUCT-05).

These tests run without PySide6/WebEngine by using the MockSettings fixture.
"""

import pytest

from fenestra.app.config import DEFAULTS
from fenestra.settings.state import _strict_bool


def test_apply_draft_validates_range(settings_state):
    """width_pct=150 exceeds max 100, should fail."""
    result = settings_state.apply_draft({"width_pct": 150})
    assert result["ok"] is False
    assert "must be between" in result["error"]


def test_apply_draft_validates_range_low(settings_state):
    """width_pct=5 is below min 10, should fail."""
    result = settings_state.apply_draft({"width_pct": 5})
    assert result["ok"] is False
    assert "must be between" in result["error"]


def test_apply_draft_keeps_press_counts_inside_the_range_and_rejects_others(settings_state):
    """The range check alone bounds snap_presses; in-range values pass unchanged."""
    assert settings_state.apply_draft({"snap_presses": 1})["applied"] == {"snap_presses": 1}
    assert settings_state.apply_draft({"snap_presses": 10})["applied"] == {"snap_presses": 10}
    assert settings_state.apply_draft({"snap_presses": 0})["ok"] is False
    assert settings_state.apply_draft({"snap_presses": 11})["ok"] is False
    assert settings_state.get_all()["snap_presses"] == 10


def test_apply_draft_rejects_unknown_keys(settings_state):
    """Unknown keys should be rejected."""
    result = settings_state.apply_draft({"nonexistent_key": "val"})
    assert result["ok"] is False
    assert "Unknown" in result["error"]


def test_apply_draft_coerces_types(settings_state):
    """String '5' should be coerced to int 5 for snap_presses."""
    result = settings_state.apply_draft({"snap_presses": "5"})
    assert result["ok"] is True
    assert result["applied"]["snap_presses"] == 5


def test_apply_draft_valid_values(settings_state):
    """Valid width and height should be accepted."""
    result = settings_state.apply_draft({"width_pct": 50, "height_pct": 80})
    assert result["ok"] is True
    assert result["applied"]["width_pct"] == 50
    assert result["applied"]["height_pct"] == 80


def test_get_all_returns_all_keys(settings_state):
    """get_all should return a dict containing every key from DEFAULTS."""
    result = settings_state.get_all()
    for key in DEFAULTS:
        assert key in result, f"Missing key: {key}"


def test_commit_draft_persists(settings_state):
    """After apply + commit, has_draft should be False."""
    settings_state.apply_draft({"width_pct": 50})
    result = settings_state.commit_draft()
    assert result["ok"] is True
    assert settings_state.has_draft is False


def test_immediate_setting_does_not_commit_unrelated_draft(settings_state, mock_settings):
    """An immediate tray setting must leave an unsaved size edit pending."""
    settings_state.apply_draft({"width_pct": 50})

    result = settings_state.persist_immediate({"minimize_to_tray": False})

    assert result == {"ok": True, "applied": {"minimize_to_tray": False}}
    assert mock_settings.minimize_to_tray is False
    assert mock_settings.width_pct == DEFAULTS["width_pct"]
    assert settings_state.get_all()["width_pct"] == 50
    assert settings_state.has_draft is True


def test_immediate_setting_supersedes_same_draft_key(settings_state, mock_settings):
    """An immediate shortcut update must remove a stale draft for that key."""
    settings_state.apply_draft({"theme": "light", "width_pct": 50})

    settings_state.persist_immediate({"theme": "dark"})

    assert mock_settings.theme == "dark"
    assert settings_state.get_all()["theme"] == "dark"
    assert settings_state.get_all()["width_pct"] == 50
    assert settings_state.has_draft is True


def test_commit_draft_reports_success_or_raises(settings_state, mock_settings):
    """The bridge relies on commit_draft never returning a failed result."""
    settings_state.apply_draft({"width_pct": 55})

    def fail_save():
        raise OSError("The registry is unavailable.")

    mock_settings.save = fail_save
    with pytest.raises(OSError):
        settings_state.commit_draft()
    assert mock_settings.width_pct == 76
    assert settings_state.has_draft

    mock_settings.save = lambda: None
    assert settings_state.commit_draft() == {"ok": True, "applied": {"width_pct": 55}}


def test_discard_draft_clears(settings_state):
    """After apply + discard, has_draft should be False."""
    settings_state.apply_draft({"width_pct": 50})
    assert settings_state.has_draft is True
    settings_state.discard_draft()
    assert settings_state.has_draft is False


def test_has_draft_initially_false(settings_state):
    """Fresh SettingsState should have no draft."""
    assert settings_state.has_draft is False


def test_get_all_overlays_draft(settings_state):
    """After applying width_pct=50, get_all should reflect the draft value."""
    settings_state.apply_draft({"width_pct": 50})
    result = settings_state.get_all()
    assert result["width_pct"] == 50


def test_get_saved_excludes_the_draft(settings_state):
    """The saved snapshot keeps persisted values until the draft is committed."""
    settings_state.apply_draft({"enable_snap": False, "game_mode_enabled": False})

    assert settings_state.get_all()["enable_snap"] is False
    assert settings_state.get_saved()["enable_snap"] is True
    assert settings_state.get_saved()["game_mode_enabled"] is True

    settings_state.commit_draft()

    assert settings_state.get_saved()["enable_snap"] is False
    assert settings_state.get_saved()["game_mode_enabled"] is False


def test_reset_to_defaults_clears_draft(settings_state):
    """reset_to_defaults should clear any pending draft."""
    settings_state.apply_draft({"width_pct": 50})
    settings_state.reset_to_defaults()
    assert settings_state.has_draft is False


# --- _strict_bool tests (BRDG-04) ---


def test_strict_bool_accepts_true_false():
    """_strict_bool should accept Python True and False."""
    assert _strict_bool(True) is True
    assert _strict_bool(False) is False


def test_strict_bool_accepts_string_true_false():
    """_strict_bool should accept string 'true'/'false' (case-insensitive)."""
    assert _strict_bool("true") is True
    assert _strict_bool("false") is False
    assert _strict_bool("TRUE") is True
    assert _strict_bool("False") is False


def test_strict_bool_accepts_int_0_1():
    """_strict_bool should accept integers 0 and 1."""
    assert _strict_bool(0) is False
    assert _strict_bool(1) is True


def test_strict_bool_rejects_ambiguous():
    """_strict_bool should reject ambiguous input with ValueError."""
    for bad_value in ("yes", "no", "on", "off", None, "", 2):
        with pytest.raises(ValueError):
            _strict_bool(bad_value)


# Strict Boolean handling and newer keys in apply_draft.


def test_apply_draft_strict_bool_prevents_false_string_bug(settings_state):
    """apply_draft({'enable_snap': 'false'}) must coerce to False, not True.

    This is the critical bug fix: Python's bool('false') returns True,
    but _strict_bool('false') correctly returns False.
    """
    result = settings_state.apply_draft({"enable_snap": "false"})
    assert result["ok"] is True
    assert result["applied"]["enable_snap"] is False


def test_apply_draft_accent_valid(settings_state):
    """apply_draft({'accent': 'teal'}) should succeed."""
    result = settings_state.apply_draft({"accent": "teal"})
    assert result["ok"] is True
    assert result["applied"]["accent"] == "teal"


def test_apply_draft_accent_invalid_defaults(settings_state):
    """apply_draft({'accent': 'neon'}) should coerce to default 'amber'."""
    result = settings_state.apply_draft({"accent": "neon"})
    assert result["ok"] is True
    assert result["applied"]["accent"] == "amber"


def test_apply_draft_density_valid(settings_state):
    """apply_draft({'density': 'compact'}) should succeed."""
    result = settings_state.apply_draft({"density": "compact"})
    assert result["ok"] is True
    assert result["applied"]["density"] == "compact"


def test_apply_draft_density_invalid_defaults(settings_state):
    """apply_draft({'density': 'huge'}) should coerce to default 'cozy'."""
    result = settings_state.apply_draft({"density": "huge"})
    assert result["ok"] is True
    assert result["applied"]["density"] == "cozy"


def test_apply_draft_minimize_to_tray(settings_state):
    """apply_draft({'minimize_to_tray': True}) should succeed."""
    result = settings_state.apply_draft({"minimize_to_tray": True})
    assert result["ok"] is True
    assert result["applied"]["minimize_to_tray"] is True


def test_apply_draft_minimize_to_tray_rejects_yes(settings_state):
    """apply_draft({'minimize_to_tray': 'yes'}) should fail (strict bool rejects 'yes')."""
    result = settings_state.apply_draft({"minimize_to_tray": "yes"})
    assert result["ok"] is False


def test_get_all_includes_new_keys(settings_state):
    """get_all() result must contain accent, density, minimize_to_tray keys."""
    result = settings_state.get_all()
    assert "accent" in result
    assert "density" in result
    assert "minimize_to_tray" in result


def test_commit_persists_new_keys(settings_state, mock_settings):
    """After apply+commit, mock_settings must have accent/density/minimize_to_tray updated."""
    settings_state.apply_draft(
        {
            "accent": "teal",
            "density": "compact",
            "minimize_to_tray": False,
        }
    )
    result = settings_state.commit_draft()
    assert result["ok"] is True
    assert mock_settings.accent == "teal"
    assert mock_settings.density == "compact"
    assert mock_settings.minimize_to_tray is False
