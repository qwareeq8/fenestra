"""Tests for theme resolution functions (QUAL-03).

Imports from fenestra.platform.theme (the actual module location).
"""

from fenestra.platform.theme import (
    get_windows_theme,
    normalize_theme_mode,
    resolve_theme,
    toggle_theme_mode,
)


def test_normalize_valid_modes():
    """Valid modes should pass through unchanged."""
    assert normalize_theme_mode("dark") == "dark"
    assert normalize_theme_mode("light") == "light"
    assert normalize_theme_mode("system") == "system"


def test_normalize_invalid_falls_back():
    """Invalid values should fall back to the default."""
    assert normalize_theme_mode("invalid") == "system"
    assert normalize_theme_mode("", "dark") == "dark"
    assert normalize_theme_mode(None) == "system"


def test_resolve_system_uses_system_theme():
    """resolve_theme('system', ...) should use the system_theme value."""
    assert resolve_theme("system", "light") == "light"
    assert resolve_theme("system", "dark") == "dark"


def test_resolve_explicit_ignores_system():
    """Explicit mode should override system_theme."""
    assert resolve_theme("dark", "light") == "dark"
    assert resolve_theme("light", "dark") == "light"


def test_toggle_from_system():
    """Toggling from 'system' should invert the resolved system theme."""
    assert toggle_theme_mode("system", "light") == "dark"
    assert toggle_theme_mode("system", "dark") == "light"


def test_toggle_from_explicit():
    """Toggling from explicit mode should swap dark<->light."""
    assert toggle_theme_mode("dark", "light") == "light"
    assert toggle_theme_mode("light", "dark") == "dark"


def test_get_windows_theme_with_mock():
    """get_windows_theme with injected read_registry should decode light/dark."""
    assert get_windows_theme(read_registry=lambda: 1) == "light"
    assert get_windows_theme(read_registry=lambda: 0) == "dark"
