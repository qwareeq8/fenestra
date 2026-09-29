"""Tests for CaptureGuard thread-safe mutex."""

import threading

from fenestra.bridge.capture_guard import CaptureGuard


def test_try_start_succeeds_first_time():
    """First try_start should succeed."""
    guard = CaptureGuard()
    assert guard.try_start() is True


def test_try_start_fails_while_active():
    """Second try_start without finish should fail."""
    guard = CaptureGuard()
    assert guard.try_start() is True
    assert guard.is_active is True
    assert guard.try_start() is False


def test_finish_allows_restart():
    """After finish, try_start should succeed again."""
    guard = CaptureGuard()
    guard.try_start()
    guard.finish()
    assert guard.is_active is False
    assert guard.try_start() is True


def test_concurrent_try_start():
    """Only one thread should win try_start in a concurrent scenario."""
    guard = CaptureGuard()
    results = []
    barrier = threading.Barrier(10)

    def attempt():
        barrier.wait()
        results.append(guard.try_start())

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert results.count(False) == 9
