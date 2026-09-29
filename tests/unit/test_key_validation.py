"""Regression tests for snap and restore key validation."""

import pytest

from fenestra.settings.key_validation import normalize_key_name, validate_key_pair


def test_normalize_key_name_accepts_defaults() -> None:
    """The shipped key names remain valid on every test platform."""
    assert normalize_key_name(" SHIFT ") == "shift"
    assert normalize_key_name("ctrl") == "ctrl"


def test_normalize_key_name_rejects_unknown_name() -> None:
    """An unmapped persisted name cannot reach keyboard hook installation."""
    with pytest.raises(ValueError, match="not recognized"):
        normalize_key_name("definitely-not-a-real-key")


def test_normalize_key_name_requires_resolver_result() -> None:
    """Windows resolver failures are converted to a stable validation error."""

    def missing(_name: str):
        raise ValueError("not mapped")

    with pytest.raises(ValueError, match="not recognized"):
        normalize_key_name("shift", scan_code_resolver=missing)


def test_validate_key_pair_rejects_case_insensitive_collision() -> None:
    """Snap and Restore cannot resolve to the same physical binding."""
    with pytest.raises(ValueError, match="must be different"):
        validate_key_pair("Shift", "shift")


def test_validate_key_pair_rejects_aliases_for_the_same_scan_code() -> None:
    """Different labels cannot bypass collision checks for one physical key."""
    scan_codes = {"shift": (42,), "left shift": (42,)}

    with pytest.raises(ValueError, match="must be different"):
        validate_key_pair(
            "shift",
            "left shift",
            scan_code_resolver=scan_codes.__getitem__,
        )


def test_settings_state_rejects_invalid_key(settings_state) -> None:
    """An invalid bridge draft is rejected before it can be persisted."""
    result = settings_state.apply_draft({"snap_key": "definitely-not-a-real-key"})
    assert result["ok"] is False
    assert settings_state.has_draft is False


def test_settings_state_rejects_pair_collision(settings_state) -> None:
    """Changing the snap key to the current restore key is atomic and rejected."""
    result = settings_state.apply_draft({"snap_key": "ctrl"})
    assert result == {"ok": False, "error": "Snap and Restore keys must be different."}
    assert settings_state.has_draft is False


def test_settings_state_accepts_atomic_pair_change(settings_state) -> None:
    """A complete distinct pair may be changed in one bridge payload."""
    result = settings_state.apply_draft({"snap_key": "ctrl", "restore_key": "alt"})
    assert result["ok"] is True
    assert result["applied"] == {"snap_key": "ctrl", "restore_key": "alt"}
