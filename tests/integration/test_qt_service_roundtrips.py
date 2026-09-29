"""Windows integration checks for Qt bridge and service lifecycle boundaries."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from PySide6 import QtCore

from fenestra.bridge.bridge import FenestraBridge
from fenestra.services.explorer_service import ExplorerService
from fenestra.services.snap import SnapService

pytestmark = [
    pytest.mark.requires_qt,
    pytest.mark.skipif(not hasattr(QtCore, "QObject"), reason="Qt required"),
]


def test_qt_settings_bridge_round_trip(settings_state) -> None:
    """A tagged draft and commit traverse real Qt signals without metadata leakage."""

    bridge = FenestraBridge(settings_state, SnapService(None))
    settings_signals: list[dict] = []
    dirty_signals: list[bool] = []
    bridge.settings_changed.connect(lambda payload: settings_signals.append(json.loads(payload)))
    bridge.dirty_changed.connect(dirty_signals.append)

    staged = json.loads(bridge.save_settings('{"width_pct": 61}', "integration-stage"))
    committed = json.loads(bridge.commit_draft("integration-commit"))

    assert staged == {"ok": True, "applied": {"width_pct": 61}}
    assert committed == {"ok": True, "applied": {"width_pct": 61}}
    assert [item["__fenestraTransaction"] for item in settings_signals] == [
        "integration-stage",
        "integration-commit",
    ]
    assert dirty_signals == [True, False]
    assert settings_state.get_all()["width_pct"] == 61
    assert "__fenestraTransaction" not in settings_state.get_all()


def test_disabled_explorer_service_start_and_stop_are_idempotent() -> None:
    """Repeated lifecycle calls do not manufacture a worker when autosize is disabled."""

    service = ExplorerService(SimpleNamespace(ex_auto_size=False))

    service.start()
    service.start()
    service.stop()
    service.stop()

    assert not service.is_running()
    assert service._thread is None
    assert service._worker is None
