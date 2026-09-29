"""Tests for the autosize engine's idle poll backoff."""

import pytest

from fenestra.workers.explorer import ExplorerAutosizeEngine, _ComIdentityRegistry


def _make_engine(tabs_fn):
    return ExplorerAutosizeEngine(
        tabs_fn,
        lambda hwnd, tab_id, target_path, dump_columns: (True, "com", False),
        lambda hwnd: True,
    )


def test_idle_backoff_decays_without_activity():
    """With a stable (empty) window set, the poll interval decays to 1 second."""
    engine = _make_engine(lambda: [])
    t = 1000.0
    fast = engine.step(t)
    assert fast <= 0.2
    settled = engine.step(t + 10.0)
    assert settled == 0.5
    idle = engine.step(t + 60.0)
    assert idle == 1.0


def test_activity_resets_backoff():
    """A window-set change restores the fast poll interval."""
    tabs = []
    engine = _make_engine(lambda: list(tabs))
    t = 1000.0
    engine.step(t)
    assert engine.step(t + 60.0) == 1.0

    tabs.append((12345, 1, 1, r"C:\somewhere", 4))
    delay = engine.step(t + 61.0)
    assert delay <= 0.2


def test_duplicate_tabs_have_independent_state_and_targets():
    """Two tabs on the same path must each receive an autosize attempt."""
    tabs = [(42, 3, 3, r"C:\same", 4), (42, 4, 4, r"C:\same", 4)]
    attempts = []
    engine = ExplorerAutosizeEngine(
        lambda: tabs,
        lambda hwnd, tab_id, path, dump_columns: (
            attempts.append((hwnd, tab_id, path)) or (True, "com", False)
        ),
        lambda hwnd: True,
    )

    engine.step(100.0)
    engine.step(100.2)

    assert set(engine.tab_state) == {(42, 3), (42, 4)}
    assert attempts == [(42, 3, r"c:\same"), (42, 4, r"c:\same")]


def test_replacement_tab_at_same_index_gets_a_fresh_autosize():
    """A new COM identity must not inherit the replaced tab's dedupe record."""
    tabs = [[(42, 100, 0, r"C:\same", 4)]]
    attempts = []
    engine = ExplorerAutosizeEngine(
        lambda: tabs[0],
        lambda hwnd, index, path, dump_columns: (
            attempts.append((hwnd, index, path)) or (True, "com", False)
        ),
        lambda hwnd: True,
    )

    engine.step(100.0)
    engine.step(100.2)
    tabs[0] = [(42, 101, 0, r"C:\same", 4)]
    engine.step(101.0)
    engine.step(101.2)

    assert attempts == [(42, 0, r"c:\same"), (42, 0, r"c:\same")]
    assert set(engine.tab_state) == {(42, 101)}


def test_stable_tab_identity_tracks_a_new_target_index():
    """A reordered tab keeps its state but sends its current collection index."""
    tabs = [[(42, 100, 0, r"C:\one", 4)]]
    attempts = []
    engine = ExplorerAutosizeEngine(
        lambda: tabs[0],
        lambda hwnd, index, path, dump_columns: (
            attempts.append((index, path)) or (True, "com", False)
        ),
        lambda hwnd: True,
    )

    engine.step(100.0)
    engine.step(100.2)
    tabs[0] = [(42, 100, 2, r"C:\two", 4)]
    engine.step(101.0)
    engine.step(101.2)

    assert attempts == [(0, r"c:\one"), (2, r"c:\two")]
    assert set(engine.tab_state) == {(42, 100)}


def test_exhausted_tab_rearms_when_its_target_index_changes():
    """A reordered tab gets a new lookup budget after positional failures."""
    tabs = [[(42, 100, 0, r"C:\same", 4)]]
    attempts = []
    engine = ExplorerAutosizeEngine(
        lambda: tabs[0],
        lambda hwnd, index, path, dump_columns: (
            attempts.append(index) or (False, "not-found", True)
        ),
        lambda hwnd: True,
        schedule=(0.05, 0.1, 0.25, 0.5, 1.0),
    )

    engine.step(100.0)
    for tenth in range(1, 101):
        engine.step(100.0 + tenth / 10)
    assert attempts == [0, 0, 0, 0, 0]

    tabs[0] = [(42, 100, 1, r"C:\same", 4)]
    for tenth in range(101, 151):
        engine.step(100.0 + tenth / 10)

    assert attempts[5:] == [1, 1, 1, 1, 1]


def test_com_identity_registry_reuses_and_releases_identities():
    """Equal COM identities share a token and absent references are released."""

    class Identity:
        def __init__(self, value):
            self.value = value

        def __eq__(self, other):
            return isinstance(other, Identity) and self.value == other.value

    registry = _ComIdentityRegistry()
    first = registry.resolve(Identity("first"))
    assert registry.resolve(Identity("first")) == first
    second = registry.resolve(Identity("second"))
    registry.retain({second})

    replacement = registry.resolve(Identity("first"))
    assert replacement not in (first, second)


def test_same_tab_index_in_separate_windows_does_not_dedupe():
    """Per-window tab indices must remain distinct across Explorer windows."""
    tabs = [(42, 0, 0, r"C:\same", 4), (43, 0, 0, r"C:\same", 4)]
    attempts = []
    engine = ExplorerAutosizeEngine(
        lambda: tabs,
        lambda hwnd, tab_id, path, dump_columns: (
            attempts.append((hwnd, tab_id, path)) or (True, "com", False)
        ),
        lambda hwnd: True,
    )

    engine.step(100.0)
    engine.step(100.2)

    assert attempts == [(42, 0, r"c:\same"), (43, 0, r"c:\same")]


def test_switching_to_details_rearms_a_parked_tab():
    """A tab skipped in List view must autosize after entering Details."""
    view_mode = [3]
    attempts = []
    engine = ExplorerAutosizeEngine(
        lambda: [(42, 3, 3, r"C:\folder", view_mode[0])],
        lambda hwnd, tab_id, path, dump_columns: (
            attempts.append(view_mode[0]) or (False, "not-details", False)
        ),
        lambda hwnd: True,
    )

    engine.step(100.0)
    engine.step(100.2)
    assert attempts == [3]

    view_mode[0] = 4
    engine._autosize = lambda hwnd, tab_id, path, dump_columns: (
        attempts.append(view_mode[0]) or (True, "com", False)
    )
    engine.step(101.0)
    engine.step(101.2)

    assert attempts == [3, 4]


@pytest.mark.parametrize("method", ["not-found", "partial"])
def test_exhausted_transient_failures_do_not_rearm_forever(method):
    """A permanently unresolved tab must converge after its finite retry budget."""
    attempts = []
    engine = ExplorerAutosizeEngine(
        lambda: [(42, 3, 3, "Control Panel", 4)],
        lambda hwnd, tab_id, path, dump_columns: attempts.append(path) or (False, method, True),
        lambda hwnd: True,
        schedule=(0.05, 0.1, 0.25, 0.5, 1.0),
    )

    engine.step(100.0)
    for tenth in range(1, 1201):
        engine.step(100.0 + tenth / 10)

    assert len(attempts) == 5
    assert not next(iter(engine.tab_state.values())).pending_retry


def test_column_diagnostics_start_after_two_failed_attempts():
    """The third retry and later ones log column metadata for diagnosis."""
    diagnostics = []
    engine = ExplorerAutosizeEngine(
        lambda: [(42, 3, 3, r"C:\folder", 4)],
        lambda hwnd, index, path, dump_columns: (
            diagnostics.append(dump_columns) or (False, "none", True)
        ),
        lambda hwnd: True,
        schedule=(0.05, 0.1, 0.25, 0.5, 1.0),
    )

    engine.step(100.0)
    for tenth in range(1, 201):
        engine.step(100.0 + tenth / 10)

    assert diagnostics == [False, False, False, True, True]


def test_retry_delay_honors_backoff_instead_of_busy_polling():
    """Pending retries should not force 20 full COM enumerations per second."""
    engine = ExplorerAutosizeEngine(
        lambda: [(42, 3, 3, r"C:\folder", 4)],
        lambda hwnd, tab_id, path, dump_columns: (False, "error", True),
        lambda hwnd: True,
    )
    engine.step(100.0)
    engine.step(100.2)

    assert engine._next_delay(100.2) > 0.19


def test_noninteractive_tab_is_rescheduled_instead_of_spinning():
    """An overdue hidden tab should not cause a 5 ms COM polling loop."""
    engine = ExplorerAutosizeEngine(
        lambda: [(42, 3, 3, r"C:\folder", 4)],
        lambda hwnd, tab_id, path, dump_columns: (True, "com", False),
        lambda hwnd: False,
    )
    engine.step(100.0)

    assert engine.step(100.2) >= 0.1


def test_hidden_windows_do_not_pin_the_fast_poll_forever():
    """Work parked on a minimized window must decay to the idle cadence.

    A background Explorer window used to hold ``pending_retry`` with a fresh
    0.5-second recheck on every pass, so the worker enumerated COM windows
    twice per second for as long as the window stayed minimized.
    """
    interactive = [False]
    attempts = []
    engine = ExplorerAutosizeEngine(
        lambda: [(42, 3, 3, r"C:\folder", 4)],
        lambda hwnd, tab_id, path, dump_columns: attempts.append(path) or (True, "com", False),
        lambda hwnd: interactive[0],
    )

    engine.step(100.0)
    engine.step(100.3)
    assert engine.step(140.0) == 1.0
    assert attempts == []

    # Restoring the window resumes the parked autosize on the next poll.
    interactive[0] = True
    engine.step(141.0)
    assert attempts == [r"c:\folder"]
    assert engine.step(141.2) == 1.0
