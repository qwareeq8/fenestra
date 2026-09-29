"""Persist settings in QSettings with validation on load and rollback on failure."""

from __future__ import annotations

import logging

from PySide6 import QtCore

from fenestra.app.config import (
    APP_NAME,
    DEFAULTS,
    ORGANIZATION,
    SETTINGS_GROUP,
    normalize_accent,
    normalize_density,
)
from fenestra.platform.theme import normalize_theme_mode
from fenestra.settings.key_validation import validate_key_pair

LOG = logging.getLogger("Fenestra")

_SETTING_KEYS = tuple(DEFAULTS)


class Settings:
    """Persistent application settings using QSettings."""

    def __init__(self, backend: QtCore.QSettings | None = None):
        """Load settings from the production backend or an injected backend."""
        if backend is None:
            self._open_backend = lambda: QtCore.QSettings(ORGANIZATION, APP_NAME)
        else:
            self._open_backend = lambda: QtCore.QSettings(backend.fileName(), backend.format())
        self._qs = backend if backend is not None else self._open_backend()
        self._qs.beginGroup(SETTINGS_GROUP)
        raw_snap_key = self._qs.value("snap_key", DEFAULTS["snap_key"], str)
        raw_restore_key = self._qs.value("restore_key", DEFAULTS["restore_key"], str)
        try:
            self.snap_key, self.restore_key = validate_key_pair(raw_snap_key, raw_restore_key)
        except ValueError as error:
            LOG.warning("Invalid persisted key bindings were reset to defaults. %s", error)
            self.snap_key = DEFAULTS["snap_key"]
            self.restore_key = DEFAULTS["restore_key"]
        self.enable_snap = _safe_bool(
            self._qs.value("enable_snap", DEFAULTS["enable_snap"], bool),
            DEFAULTS["enable_snap"],
        )
        # Bounds mirror SettingsState.KEYS: a corrupted or hand-edited registry
        # value must not reach the geometry math (width_pct=0 would create a
        # zero-size window; 150 would push it off-screen).
        self.snap_presses = _safe_int(
            self._qs.value("snap_presses", DEFAULTS["snap_presses"], int),
            DEFAULTS["snap_presses"],
            bounds=(1, 10),
        )
        self.snap_interval = _safe_int(
            self._qs.value("snap_interval", DEFAULTS["snap_interval"], int),
            DEFAULTS["snap_interval"],
            bounds=(100, 5000),
        )
        self.width_pct = _safe_int(
            self._qs.value("width_pct", DEFAULTS["width_pct"], int),
            DEFAULTS["width_pct"],
            bounds=(10, 100),
        )
        self.height_pct = _safe_int(
            self._qs.value("height_pct", DEFAULTS["height_pct"], int),
            DEFAULTS["height_pct"],
            bounds=(10, 100),
        )
        self.ex_auto_size = _safe_bool(
            self._qs.value("ex_auto_size", DEFAULTS["ex_auto_size"], bool),
            DEFAULTS["ex_auto_size"],
        )
        self.game_mode_enabled = _safe_bool(
            self._qs.value("game_mode_enabled", DEFAULTS["game_mode_enabled"], bool),
            DEFAULTS["game_mode_enabled"],
        )
        self.run_at_startup = _safe_bool(
            self._qs.value("run_at_startup", DEFAULTS["run_at_startup"], bool),
            DEFAULTS["run_at_startup"],
        )
        self.theme = normalize_theme_mode(
            self._qs.value("theme", DEFAULTS["theme"], str),
            DEFAULTS["theme"],
        )
        self.accent = normalize_accent(self._qs.value("accent", DEFAULTS["accent"], str))
        self.density = normalize_density(self._qs.value("density", DEFAULTS["density"], str))
        self.minimize_to_tray = _safe_bool(
            self._qs.value("minimize_to_tray", DEFAULTS["minimize_to_tray"], bool),
            DEFAULTS["minimize_to_tray"],
        )
        self._qs.endGroup()

    def save(self):
        """Persist owned keys without deleting the settings group first.

        A failed write is rolled back to the values observed before this save.
        Unknown keys are deliberately preserved for forward compatibility.
        """
        self.snap_key, self.restore_key = validate_key_pair(self.snap_key, self.restore_key)

        self._qs.beginGroup(SETTINGS_GROUP)
        previous = {
            key: (self._qs.contains(key), self._qs.value(key) if self._qs.contains(key) else None)
            for key in _SETTING_KEYS
        }
        try:
            for key in _SETTING_KEYS:
                self._qs.setValue(key, getattr(self, key))
        except Exception:
            self._qs.endGroup()
            self._restore_previous_values(previous)
            raise
        else:
            self._qs.endGroup()

        # Flush to the backing store and surface a write failure to the caller
        # instead of silently reporting a clean save.
        self._qs.sync()
        if self._qs.status() != QtCore.QSettings.Status.NoError:
            status = self._qs.status()
            self._restore_previous_values(previous)
            self._reopen_backend()
            raise OSError(f"QSettings write failed with status {status}.")

    def _reopen_backend(self) -> None:
        """Replace the store after a failed write.

        QSettings keeps the first error status for the lifetime of the object,
        so without a fresh object every later save would report that failure.
        """
        open_backend = getattr(self, "_open_backend", None)
        if open_backend is not None:
            self._qs = open_backend()

    def _restore_previous_values(self, previous: dict) -> None:
        """Best-effort rollback for a failed save operation."""
        group_open = False
        try:
            self._qs.beginGroup(SETTINGS_GROUP)
            group_open = True
            for key, (existed, value) in previous.items():
                if existed:
                    self._qs.setValue(key, value)
                else:
                    self._qs.remove(key)
            self._qs.sync()
        except Exception:
            LOG.exception("Rolling back a failed settings write also failed.")
        finally:
            if group_open:
                self._qs.endGroup()


def _safe_int(val, default, bounds=None):
    """Coerce to int, falling back to default; clamp into bounds when given."""
    try:
        if val is None:
            return default
        result = int(val)
    except Exception:
        return default
    if bounds is not None:
        lo, hi = bounds
        result = max(lo, min(hi, result))
    return result


def _safe_bool(val, default):
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    text = str(val).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    try:
        return bool(int(val))
    except Exception:
        return default
