"""Tests for the pure Python key-capture session."""

import threading
import time
from types import SimpleNamespace

from fenestra.workers.key_capture import KeyCaptureSession


class _Keyboard:
    """Record the hook and deliver events to it from the test."""

    def __init__(self):
        self.callback = None
        self.hooked = threading.Event()
        self.unhooked = []

    def hook(self, callback):
        self.callback = callback
        self.hooked.set()
        return "hook"

    def unhook(self, hook_id):
        self.unhooked.append(hook_id)


def _run_in_thread(session):
    result = {}
    thread = threading.Thread(target=lambda: result.update(value=session.run()))
    thread.start()
    return thread, result


def test_capture_returns_the_first_pressed_key_and_unhooks():
    keyboard = _Keyboard()
    session = KeyCaptureSession(keyboard)
    thread, result = _run_in_thread(session)
    assert keyboard.hooked.wait(1)

    keyboard.callback(SimpleNamespace(event_type="up", name="a"))
    keyboard.callback(SimpleNamespace(event_type="down", name="F8"))
    thread.join(1)

    assert result["value"] == ("f8", "captured")
    assert keyboard.unhooked == ["hook"]


def test_escape_cancels_the_capture():
    keyboard = _Keyboard()
    session = KeyCaptureSession(keyboard)
    thread, result = _run_in_thread(session)
    assert keyboard.hooked.wait(1)

    keyboard.callback(SimpleNamespace(event_type="down", name="esc"))
    thread.join(1)

    assert result["value"] == (None, "esc")


def test_stop_wakes_the_capture_immediately():
    """A stop request ends the wait at once instead of after a polling tick."""
    keyboard = _Keyboard()
    session = KeyCaptureSession(keyboard, timeout_s=None)
    thread, result = _run_in_thread(session)
    assert keyboard.hooked.wait(1)

    started = time.monotonic()
    session.stop()
    thread.join(1)

    assert not thread.is_alive()
    assert time.monotonic() - started < 0.5
    assert result["value"] == (None, "stopped")
    assert keyboard.unhooked == ["hook"]


def test_capture_times_out_without_a_key():
    keyboard = _Keyboard()
    session = KeyCaptureSession(keyboard, timeout_s=0.05)

    assert session.run() == (None, "timeout")
    assert keyboard.unhooked == ["hook"]


def test_stop_before_run_never_installs_the_hook():
    keyboard = _Keyboard()
    session = KeyCaptureSession(keyboard)
    session.stop()

    assert session.run() == (None, "stopped")
    assert keyboard.callback is None
