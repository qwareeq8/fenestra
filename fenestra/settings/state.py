"""Settings state management for the FenestraBridge.

Reads all settings keys from QSettings via the existing Settings class
and returns them as a JSON-serializable dict. Validates and writes settings
back via a draft/commit model: changes accumulate in a draft dict, Save
persists to QSettings and applies side effects, Discard reverts to persisted
values.
"""

import json
from typing import ClassVar

from fenestra.app.config import (
    DEFAULTS,
    normalize_accent,
    normalize_density,
    normalize_snap_presses,
)
from fenestra.platform.theme import normalize_theme_mode
from fenestra.settings.key_validation import normalize_key_name, validate_key_pair
from fenestra.settings.persistence import Settings


def _strict_bool(value):
    """Parse strict boolean values and reject ambiguous input.

    Accepted values are ``True``, ``False``, ``"true"``, ``"false"``, ``1``,
    and ``0``. Values such as ``"yes"``, ``"no"``, ``"on"``, ``"off"``,
    ``None``, and other non-boolean strings are rejected.

    This prevents the ``bool("false") is True`` pitfall at the bridge boundary.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        raise ValueError(f"Expected 0 or 1; received {value}.")
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower == "true":
            return True
        if lower == "false":
            return False
        raise ValueError(f"Expected 'true' or 'false'; received {value!r}.")
    raise ValueError(f"Cannot convert {type(value).__name__} to a Boolean value.")


class SettingsState:
    """Thin wrapper around Settings that provides JSON-friendly read/write.

    The draft and commit model validates and stages changes in ``_draft``,
    persists staged changes only on commit, and allows staged changes to be
    discarded. ``get_all()`` overlays staged values on persisted values, while
    ``has_draft`` reports whether unsaved changes exist.
    """

    # This exhaustive key list maps names to a coercer and optional valid range.
    KEYS: ClassVar[dict] = {
        "snap_key": (normalize_key_name, None),
        "restore_key": (normalize_key_name, None),
        "enable_snap": (_strict_bool, None),
        "snap_presses": (int, (1, 10)),
        "snap_interval": (int, (100, 5000)),
        "width_pct": (int, (10, 100)),
        "height_pct": (int, (10, 100)),
        "ex_auto_size": (_strict_bool, None),
        "game_mode_enabled": (_strict_bool, None),
        "run_at_startup": (_strict_bool, None),
        "theme": (str, None),
        "accent": (str, None),
        "density": (str, None),
        "minimize_to_tray": (_strict_bool, None),
    }

    def __init__(self, settings: Settings):
        self._settings = settings
        self._draft = None  # ``None`` means that no changes are pending.

    def get_all(self) -> dict:
        """Return all settings as a JSON-serializable dict.

        Returns persisted settings overlaid with any draft values.
        Normalization applies after the overlay so draft values are
        also normalized.
        """
        return self._snapshot(include_draft=True)

    def get_saved(self) -> dict:
        """Return the persisted settings without the draft overlay.

        Returns
        -------
        dict
            Every settings key with the value that is in effect, normalized
            as ``get_all`` normalizes it.
        """
        return self._snapshot(include_draft=False)

    def _snapshot(self, *, include_draft: bool) -> dict:
        """Return normalized settings, optionally overlaid with the draft."""
        result = {}
        for key, (coercer, _) in self.KEYS.items():
            val = getattr(self._settings, key, DEFAULTS.get(key))
            result[key] = coercer(val)
        # Overlay draft values before normalization.
        if include_draft and self._draft:
            result.update(self._draft)
        # Normalize the theme.
        result["theme"] = normalize_theme_mode(result["theme"], DEFAULTS["theme"])
        # Normalize the snap press count.
        result["snap_presses"] = normalize_snap_presses(result["snap_presses"])
        result["snap_key"], result["restore_key"] = validate_key_pair(
            result["snap_key"], result["restore_key"]
        )
        result["accent"] = normalize_accent(result["accent"])
        result["density"] = normalize_density(result["density"])
        return result

    def get_json(self) -> str:
        """Return all settings as a JSON string."""
        return json.dumps(self.get_all())

    @property
    def has_draft(self) -> bool:
        """Return whether unsaved changes exist."""
        return self._draft is not None and len(self._draft) > 0

    def get_persisted(self, key: str):
        """Return one persisted setting without exposing the storage object."""
        if key not in self.KEYS:
            raise KeyError(f"Unknown settings key: {key!r}.")
        return getattr(self._settings, key)

    def apply_draft(self, data: dict) -> dict:
        """Validate and store changes in draft (not persisted).

        Returns {"ok": True, "applied": {key: value, ...}} on success.
        Returns {"ok": False, "error": "..."} on validation failure or
        unknown keys.
        """
        result = self._validate(data)
        if not result.get("ok"):
            return result
        validated = result["applied"]

        if self._draft is None:
            self._draft = {}
        self._draft.update(validated)

        return result

    def persist_immediate(self, data: dict) -> dict:
        """Persist validated keys without committing unrelated draft values.

        Immediate controls, such as tray-menu toggles and the Ctrl+T theme
        shortcut, must not flush edits that are still waiting for Save. An
        immediate value supersedes a draft value for the same key while all
        other draft keys remain pending.
        """
        result = self._validate(data)
        if not result.get("ok"):
            return result
        validated = result["applied"]
        previous = {key: getattr(self._settings, key) for key in validated}
        try:
            for key, value in validated.items():
                setattr(self._settings, key, value)
            self._settings.save()
        except Exception:
            for key, value in previous.items():
                setattr(self._settings, key, value)
            raise

        if self._draft:
            for key in validated:
                self._draft.pop(key, None)
            if not self._draft:
                self._draft = None
        return result

    def _validate(self, data: dict) -> dict:
        """Validate a settings patch and return its normalized values."""
        unknown = [k for k in data if k not in self.KEYS]
        if unknown:
            return {"ok": False, "error": f"Unknown settings keys: {unknown}."}

        validated = {}
        for key, value in data.items():
            coercer, bounds = self.KEYS[key]
            try:
                coerced = coercer(value)
            except (ValueError, TypeError) as e:
                return {"ok": False, "error": f"Invalid value for {key!r}: {e}"}
            if bounds is not None:
                lo, hi = bounds
                if not (lo <= coerced <= hi):
                    return {
                        "ok": False,
                        "error": (f"{key!r} must be between {lo} and {hi}; received {coerced}."),
                    }
            if key == "theme":
                coerced = normalize_theme_mode(coerced, DEFAULTS["theme"])
            if key == "accent":
                coerced = normalize_accent(coerced)
            if key == "density":
                coerced = normalize_density(coerced)
            validated[key] = coerced

        if "snap_key" in validated or "restore_key" in validated:
            current = self.get_all()
            candidate_snap = validated.get("snap_key", current["snap_key"])
            candidate_restore = validated.get("restore_key", current["restore_key"])
            try:
                candidate_snap, candidate_restore = validate_key_pair(
                    candidate_snap, candidate_restore
                )
            except ValueError as error:
                return {"ok": False, "error": str(error)}
            if "snap_key" in validated:
                validated["snap_key"] = candidate_snap
            if "restore_key" in validated:
                validated["restore_key"] = candidate_restore

        return {"ok": True, "applied": validated}

    def commit_draft(self) -> dict:
        """Persist the draft to QSettings, clear it, and return applied changes."""
        if not self._draft:
            return {"ok": True, "applied": {}}
        applied = dict(self._draft)
        previous = {key: getattr(self._settings, key) for key in applied}
        try:
            for key, value in applied.items():
                setattr(self._settings, key, value)
            self._settings.save()
        except Exception:
            for key, value in previous.items():
                setattr(self._settings, key, value)
            raise
        self._draft = None
        return {"ok": True, "applied": applied}

    def discard_draft(self):
        """Clear draft without persisting."""
        self._draft = None

    def reset_to_defaults(self) -> dict:
        """Reset all settings to defaults and return the new state."""
        previous_draft = self._draft
        previous = {key: getattr(self._settings, key) for key in DEFAULTS}
        self._draft = None
        try:
            for key, val in DEFAULTS.items():
                setattr(self._settings, key, val)
            self._settings.save()
        except Exception:
            for key, val in previous.items():
                setattr(self._settings, key, val)
            self._draft = previous_draft
            raise
        return self.get_all()
