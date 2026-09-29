"""Regression tests for non-destructive and failure-safe settings writes."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6 import QtCore

from fenestra.app.config import DEFAULTS, SETTINGS_GROUP
from fenestra.settings.persistence import Settings
from fenestra.settings.state import SettingsState


@pytest.fixture(autouse=True)
def _qsettings_status_api(monkeypatch):
    """Provide the one QSettings enum used by these dependency-light tests."""
    if not hasattr(QtCore, "QSettings"):
        monkeypatch.setattr(
            QtCore,
            "QSettings",
            SimpleNamespace(Status=SimpleNamespace(NoError=0)),
            raising=False,
        )


class FakeQSettings:
    """Small in-memory QSettings model with injectable write failure."""

    def __init__(self, values=None, fail_key=None, status=None):
        self.values = dict(values or {})
        self.fail_key = fail_key
        self.status_value = status
        self.failed = False
        self.group = None
        self.removed = []

    def beginGroup(self, group):
        assert self.group is None
        self.group = group

    def endGroup(self):
        assert self.group == SETTINGS_GROUP
        self.group = None

    def contains(self, key):
        return key in self.values

    def value(self, key, default=None, value_type=None):
        value = self.values.get(key, default)
        return value_type(value) if value_type is not None else value

    def setValue(self, key, value):
        if key == self.fail_key and not self.failed:
            self.failed = True
            raise OSError("Simulated write failure.")
        self.values[key] = value

    def remove(self, key):
        self.removed.append(key)
        self.values.pop(key, None)

    def sync(self):
        return None

    def status(self):
        if self.status_value is None:
            return QtCore.QSettings.Status.NoError
        return self.status_value


def _settings_with_store(store: FakeQSettings) -> Settings:
    settings = Settings.__new__(Settings)
    settings._qs = store
    for key, value in DEFAULTS.items():
        setattr(settings, key, value)
    return settings


def test_save_preserves_unknown_values_and_never_clears_group() -> None:
    """Saving owned settings leaves a forward-compatible unknown value intact."""
    store = FakeQSettings({"future_setting": "keep-me"})
    settings = _settings_with_store(store)
    settings.width_pct = 64

    settings.save()

    assert store.values["future_setting"] == "keep-me"
    assert store.values["width_pct"] == 64
    assert "" not in store.removed


def test_save_rolls_back_values_when_a_write_raises() -> None:
    """A mid-save exception restores every previously observed owned key."""
    original = {**DEFAULTS, "future_setting": "keep-me"}
    store = FakeQSettings(deepcopy(original), fail_key="height_pct")
    settings = _settings_with_store(store)
    settings.width_pct = 55
    settings.height_pct = 66

    with pytest.raises(OSError, match=r"Simulated write failure\."):
        settings.save()

    assert store.values == original
    assert store.group is None


def test_save_rolls_back_values_when_qsettings_reports_failure() -> None:
    """A registry flush status error cannot leave the new values in memory."""
    original = {**DEFAULTS, "future_setting": "keep-me"}
    store = FakeQSettings(deepcopy(original), status=1)
    settings = _settings_with_store(store)
    settings.width_pct = 55

    with pytest.raises(OSError, match="QSettings write failed"):
        settings.save()

    assert store.values == original
    assert store.group is None


def test_settings_load_falls_back_from_invalid_key_pair(monkeypatch) -> None:
    """Corrupt persisted key names cannot crash listener construction at startup."""
    store = FakeQSettings({"snap_key": "not-a-real-key", "restore_key": "ctrl"})
    monkeypatch.setattr(QtCore, "QSettings", lambda *_args: store, raising=False)

    settings = Settings()

    assert settings.snap_key == DEFAULTS["snap_key"]
    assert settings.restore_key == DEFAULTS["restore_key"]


def test_settings_uses_an_injected_backend_without_constructing_qsettings(monkeypatch) -> None:
    """Smoke tests can exercise persistence without opening the production registry."""
    store = FakeQSettings()
    constructor = MagicMock(side_effect=AssertionError("production backend opened"))
    monkeypatch.setattr(QtCore, "QSettings", constructor, raising=False)

    settings = Settings(store)

    assert settings._qs is store
    constructor.assert_not_called()


def test_commit_failure_restores_in_memory_settings(mock_settings) -> None:
    """A failed persistence call leaves runtime values at their prior state."""
    state = SettingsState(mock_settings)
    state.apply_draft({"width_pct": 55, "height_pct": 66})

    def fail_save():
        raise OSError("registry unavailable")

    mock_settings.save = fail_save
    with pytest.raises(OSError, match="registry unavailable"):
        state.commit_draft()

    assert mock_settings.width_pct == DEFAULTS["width_pct"]
    assert mock_settings.height_pct == DEFAULTS["height_pct"]
    assert state.has_draft is True


def test_reset_failure_restores_runtime_values_and_draft(mock_settings) -> None:
    """A failed reset cannot leave defaults in memory or discard staged edits."""
    mock_settings.width_pct = 61
    state = SettingsState(mock_settings)
    state.apply_draft({"height_pct": 67})

    def fail_save():
        raise OSError("registry unavailable")

    mock_settings.save = fail_save
    with pytest.raises(OSError, match="registry unavailable"):
        state.reset_to_defaults()

    assert mock_settings.width_pct == 61
    assert state.get_all()["height_pct"] == 67
    assert state.has_draft is True


def test_failed_sync_reopens_the_store_so_later_saves_can_succeed() -> None:
    """A sticky QSettings error status must not fail every later save."""
    failing = FakeQSettings(status="AccessError")
    healthy = FakeQSettings()
    settings = _settings_with_store(failing)
    settings._open_backend = lambda: healthy

    with pytest.raises(OSError):
        settings.save()
    settings.width_pct = 61
    settings.save()

    assert settings._qs is healthy
    assert healthy.values["width_pct"] == 61
