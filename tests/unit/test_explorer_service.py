"""Tests for Explorer autosize worker lifecycle recovery."""

import sys
from types import SimpleNamespace

from fenestra.services import explorer_service


class _StoppedThread:
    def isRunning(self):
        return False


class _StoppingThread:
    def __init__(self):
        self.running = True

    def isRunning(self):
        return self.running

    def quit(self):
        pass

    def wait(self, timeout):
        return False


class _Worker:
    def stop(self):
        pass


def test_autosize_callback_forwards_the_tab_and_the_diagnostics_request(monkeypatch):
    """One service callback addresses the tab and passes the diagnostics flag through."""
    calls = []

    def autosize(*args, **kwargs):
        calls.append((args, kwargs))
        return True, "com"

    fake_module = SimpleNamespace(autosize_explorer_columns=autosize)
    monkeypatch.setitem(sys.modules, "fenestra.services.explorer_columns", fake_module)

    assert explorer_service._autosize_explorer_columns(42, 3, r"C:\Data") == (True, "com")
    assert explorer_service._autosize_explorer_columns(42, 3, r"C:\Data", dump_columns=True) == (
        True,
        "com",
    )

    assert calls == [
        ((42, 3), {"target_path": r"C:\Data", "dump_columns": False}),
        ((42, 3), {"target_path": r"C:\Data", "dump_columns": True}),
    ]


def test_unexpected_worker_exit_schedules_bounded_restart(monkeypatch):
    """An enabled worker should recover after an unexpected thread exit."""
    callbacks = []
    monkeypatch.setattr(
        explorer_service.QtCore.QTimer,
        "singleShot",
        lambda delay, callback: callbacks.append((delay, callback)),
    )
    service = explorer_service.ExplorerService(SimpleNamespace(ex_auto_size=True))
    service._thread = _StoppedThread()
    service._worker = object()

    service._on_finished()

    assert service._thread is None
    assert service._worker is None
    assert service._restart_scheduled
    assert callbacks[0][0] == 1000


def test_expected_worker_exit_does_not_restart(monkeypatch):
    """An explicit stop must not be mistaken for a worker crash."""
    callbacks = []
    monkeypatch.setattr(
        explorer_service.QtCore.QTimer,
        "singleShot",
        lambda delay, callback: callbacks.append((delay, callback)),
    )
    service = explorer_service.ExplorerService(SimpleNamespace(ex_auto_size=True))
    service._thread = _StoppedThread()
    service._worker = object()
    service._stop_requested = True

    service._on_finished()

    assert callbacks == []


def test_start_requested_during_timed_out_stop_runs_after_exit(monkeypatch):
    """A delayed worker exit must honor a newer request to enable autosizing."""
    callbacks = []
    monkeypatch.setattr(
        explorer_service.QtCore.QTimer,
        "singleShot",
        lambda delay, callback: callbacks.append((delay, callback)),
    )
    service = explorer_service.ExplorerService(SimpleNamespace(ex_auto_size=True))
    thread = _StoppingThread()
    service._thread = thread
    service._worker = _Worker()

    service.stop()
    service.start()
    assert service._start_when_stopped

    thread.running = False
    service._on_finished(thread)

    assert service._thread is None
    assert callbacks[0][0] == 0
    assert callbacks[0][1] == service._start_worker


def test_stale_thread_finish_cannot_clear_current_worker():
    """A queued finish signal from an older generation is ignored."""
    service = explorer_service.ExplorerService(SimpleNamespace(ex_auto_size=True))
    stale = _StoppedThread()
    current = _StoppingThread()
    worker = object()
    service._thread = current
    service._worker = worker

    service._on_finished(stale)

    assert service._thread is current
    assert service._worker is worker
