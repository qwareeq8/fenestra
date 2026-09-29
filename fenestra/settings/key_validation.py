"""Validation helpers for the global snap and restore key bindings."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Iterable

# Fenestra is Windows-only, but its pure unit tests collect on Linux. This set
# mirrors the named keys exposed by keyboard 0.13.5 on Windows so off-Windows
# validation remains deterministic without probing /dev/input or invoking
# dumpkeys. Printable single-character keys are handled separately.
_PORTABLE_NAMED_KEYS = frozenset(
    {
        "alt",
        "alt gr",
        "applications",
        "attn",
        "backspace",
        "browser back",
        "browser favorites",
        "browser forward",
        "browser refresh",
        "browser search key",
        "browser start and home",
        "browser stop",
        "caps lock",
        "clear",
        "control-break processing",
        "crsel",
        "ctrl",
        "decimal",
        "delete",
        "down",
        "end",
        "enter",
        "erase eof",
        "esc",
        "execute",
        "exsel",
        "help",
        "home",
        "ime accept",
        "ime convert",
        "ime final mode",
        "ime hangul mode",
        "ime junja mode",
        "ime kanji mode",
        "ime mode change request",
        "ime nonconvert",
        "ime process",
        "insert",
        "left",
        "left alt",
        "left ctrl",
        "left menu",
        "left shift",
        "left windows",
        "menu",
        "next track",
        "num lock",
        "pa1",
        "page down",
        "page up",
        "pause",
        "play",
        "play/pause media",
        "previous track",
        "print",
        "print screen",
        "right",
        "right alt",
        "right ctrl",
        "right menu",
        "right shift",
        "right windows",
        "scroll lock",
        "select",
        "select media",
        "separator",
        "shift",
        "sleep",
        "space",
        "spacebar",
        "start application 1",
        "start application 2",
        "start mail",
        "stop media",
        "tab",
        "up",
        "volume down",
        "volume mute",
        "volume up",
        "windows",
        "zoom",
    }
)
_FUNCTION_KEY_PATTERN = re.compile(r"f(?:[1-9]|1\d|2[0-4])\Z")


def _runtime_scan_code_resolver() -> Callable[[str], Iterable[int]] | None:
    """Return keyboard's scan-code resolver when the real API is available."""
    try:
        import keyboard
    except ImportError:
        return None
    resolver = getattr(keyboard, "key_to_scan_codes", None)
    return resolver if callable(resolver) else None


def _normalize_with_keyboard(name: str) -> str:
    """Use keyboard's aliases when available without requiring them in tests."""
    try:
        import keyboard

        normalizer = getattr(keyboard, "normalize_name", None)
        if callable(normalizer):
            return str(normalizer(name))
    except Exception:
        pass
    return name


def normalize_key_name(
    value: object,
    *,
    scan_code_resolver: Callable[[str], Iterable[int]] | None = None,
) -> str:
    """Return a normalized single key name or raise ``ValueError``.

    Windows release builds ask the keyboard package to resolve the name to at
    least one scan code. Off-Windows tests use a deterministic mirror of the
    Windows names because probing the host keyboard there can require elevated
    access and external utilities.
    """
    if not isinstance(value, str):
        raise ValueError("A key binding must be a string.")
    name = _normalize_with_keyboard(value.strip().lower())
    if not name:
        raise ValueError("A key binding cannot be empty.")

    if scan_code_resolver is not None:
        resolver = scan_code_resolver
    elif sys.platform == "win32":
        # Native dependencies are intentionally stubbed in lightweight unit
        # tests. Fall back to the portable Windows-name table only when the
        # stub does not expose keyboard's real resolver API.
        resolver = _runtime_scan_code_resolver()
    else:
        resolver = None

    if resolver is not None:
        try:
            scan_codes = tuple(resolver(name))
        except Exception as error:
            raise ValueError(f"The key name {name!r} is not recognized.") from error
        if not scan_codes:
            raise ValueError(f"The key name {name!r} is not recognized.")
    elif not (
        (len(name) == 1 and name.isprintable())
        or name in _PORTABLE_NAMED_KEYS
        or _FUNCTION_KEY_PATTERN.fullmatch(name)
    ):
        raise ValueError(f"The key name {name!r} is not recognized.")

    return name


def validate_key_pair(
    snap_key: object,
    restore_key: object,
    *,
    scan_code_resolver: Callable[[str], Iterable[int]] | None = None,
) -> tuple[str, str]:
    """Return a valid, distinct snap and restore key pair."""
    snap = normalize_key_name(snap_key, scan_code_resolver=scan_code_resolver)
    restore = normalize_key_name(restore_key, scan_code_resolver=scan_code_resolver)
    if snap == restore:
        raise ValueError("Snap and Restore keys must be different.")

    resolver = scan_code_resolver
    if resolver is None and sys.platform == "win32":
        resolver = _runtime_scan_code_resolver()
    if resolver is not None:
        snap_codes = set(resolver(snap))
        restore_codes = set(resolver(restore))
        if snap_codes & restore_codes:
            raise ValueError("Snap and Restore keys must be different.")
    return snap, restore
