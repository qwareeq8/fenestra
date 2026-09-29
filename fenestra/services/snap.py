"""Snap service, MultiPressHotkeyListener, and SnapRestoreController engine.

MultiPressHotkeyListener detects multi-press keyboard patterns and emits a trigger signal.
SnapRestoreController performs window snap and restore operations.
SnapService wraps both so bridge.py can trigger snap actions without depending
on class internals.
"""

import ctypes
import logging
import os
import secrets
import threading
import time
from collections import deque
from ctypes import wintypes

import keyboard
import win32con
import win32gui
from PySide6 import QtCore

from fenestra.app.config import DEFAULTS, normalize_snap_presses
from fenestra.platform.win32_abi import DWORD, KERNEL32, handle_value
from fenestra.platform.win32_helpers import (
    USER32,
    _exit_fullscreen,
    _get_window_dwm_rect,
    _is_window_fullscreen,
    _is_window_hidden_from_user,
    _is_window_interactive,
    _should_skip_snap_for_game,
    get_monitor_rect,
)
from fenestra.settings.key_validation import validate_key_pair

LOG = logging.getLogger("Fenestra")

_CENTER_ONLY_EXECUTABLES = frozenset({"googledrivefs.exe", "lightbulb.exe"})
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WINDOW_IDENTITY_PROPERTY = "Fenestra.SnapRestoreIdentity"
_TEST_TARGET_SCAN_LIMIT = 512


def window_border_deltas(
    win_rect: tuple[int, int, int, int],
    visible_rect: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int]:
    """Return (left, top, right, bottom) invisible-border widths.

    DWM windows extend past their visible frame; centering on the raw window
    rect leaves the window visually off-center. Deltas are clamped to a sane
    range so a bogus DWM answer cannot fling the window off-screen.
    """
    if visible_rect is None:
        return (0, 0, 0, 0)
    wl, wt, wr, wb = win_rect
    vl, vt, vr, vb = visible_rect
    clamp = lambda v: max(0, min(64, v))  # noqa: E731
    return (clamp(vl - wl), clamp(vt - wt), clamp(wr - vr), clamp(wb - vb))


def calculate_snap_position(
    monitor_left: int,
    monitor_top: int,
    monitor_width: int,
    monitor_height: int,
    width_pct: int,
    height_pct: int,
    border_deltas: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> tuple:
    """Calculate a DWM-aware snap target ``(x, y, width, height)``."""
    visible_w = monitor_width * width_pct // 100
    visible_h = monitor_height * height_pct // 100
    border_l, border_t, border_r, border_b = border_deltas
    w = visible_w + border_l + border_r
    h = visible_h + border_t + border_b
    x = monitor_left + (monitor_width - visible_w) // 2 - border_l
    y = monitor_top + (monitor_height - visible_h) // 2 - border_t
    return (x, y, w, h)


def calculate_center_position(
    monitor_left: int,
    monitor_top: int,
    monitor_width: int,
    monitor_height: int,
    window_width: int,
    window_height: int,
    border_deltas: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> tuple[int, int]:
    """Center an existing window without changing its raw window size."""
    border_l, border_t, border_r, border_b = border_deltas
    visible_w = max(1, window_width - border_l - border_r)
    visible_h = max(1, window_height - border_t - border_b)
    x = monitor_left + (monitor_width - visible_w) // 2 - border_l
    y = monitor_top + (monitor_height - visible_h) // 2 - border_t
    return x, y


def should_center_without_resize(window_style: int, process_name: str) -> bool:
    """Return whether snapping must preserve the window's current size."""
    return not bool(window_style & win32con.WS_SIZEBOX) or (
        process_name.casefold() in _CENTER_ONLY_EXECUTABLES
    )


def _window_identity(hwnd: int) -> tuple[int, int, str] | None:
    """Return process, thread, and class identity for an HWND."""
    pid = DWORD()
    thread_id = int(USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)))
    if not thread_id or not pid.value:
        return None
    try:
        class_name = str(win32gui.GetClassName(hwnd) or "")
    except Exception:
        class_name = ""
    return int(pid.value), thread_id, class_name


def _window_process_name(hwnd: int) -> str:
    """Return the lowercased executable basename that owns an HWND."""
    pid = DWORD()
    USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    process = KERNEL32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = DWORD(len(buffer))
        if not KERNEL32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(length)):
            return ""
        return os.path.basename(buffer.value).casefold()
    except Exception:
        return ""
    finally:
        KERNEL32.CloseHandle(process)


def _install_window_identity_token(hwnd: int) -> int | None:
    """Tag a live HWND so numeric-handle reuse can be detected exactly."""
    token = secrets.randbits(ctypes.sizeof(ctypes.c_void_p) * 8) or 1
    try:
        if USER32.SetPropW(hwnd, _WINDOW_IDENTITY_PROPERTY, token):
            return token
    except Exception:
        LOG.debug("Snap: could not install an HWND identity token.", exc_info=True)
    return None


def _window_identity_matches(hwnd: int, original: dict[str, object]) -> bool:
    """Return whether an HWND still denotes the window recorded at snap time."""
    expected_identity = original.get("identity")
    if expected_identity is not None and _window_identity(hwnd) != expected_identity:
        return False
    expected_token = original.get("identity_token")
    if expected_token is None:
        return True
    try:
        return handle_value(USER32.GetPropW(hwnd, _WINDOW_IDENTITY_PROPERTY)) == expected_token
    except Exception:
        return False


def _remove_window_identity_token(hwnd: int, original: dict[str, object]) -> None:
    """Remove Fenestra's property only when it still belongs to this record."""
    expected_token = original.get("identity_token")
    if expected_token is None:
        return
    try:
        if handle_value(USER32.GetPropW(hwnd, _WINDOW_IDENTITY_PROPERTY)) == expected_token:
            USER32.RemovePropW(hwnd, _WINDOW_IDENTITY_PROPERTY)
    except Exception:
        LOG.debug("Snap: could not remove an HWND identity token.", exc_info=True)


def _scan_codes(*keys: str) -> frozenset[int]:
    """Return the scan codes of the given key names, skipping unknown names."""
    codes: set[int] = set()
    for key in keys:
        try:
            codes.update(keyboard.key_to_scan_codes(key))
        except (KeyError, ValueError):
            LOG.warning("Key %r has no scan code; chords with it are not detected.", key)
    return frozenset(codes)


def _unhook(hook) -> None:
    """Remove one keyboard hook and log, rather than hide, a failure."""
    try:
        keyboard.unhook(hook)
    except Exception:
        LOG.warning("A keyboard hook could not be removed.", exc_info=True)


class MultiPressHotkeyListener(QtCore.QObject):
    """Detect multi-press keyboard patterns and emit a trigger signal."""

    triggered = QtCore.Signal(bool)

    def __init__(self, settings, capture_guard=None):
        super().__init__()
        self.settings = settings
        self._capture_guard = capture_guard
        self._press_times: deque[float] = deque(
            maxlen=normalize_snap_presses(self.settings.snap_presses)
        )
        self._press_lock = threading.Lock()
        self._held = False
        self._last_press_event = 0.0
        try:
            self.current_key, self.restore_key = validate_key_pair(
                settings.snap_key, getattr(settings, "restore_key", DEFAULTS["restore_key"])
            )
        except ValueError as error:
            LOG.warning("Invalid runtime key bindings were reset to defaults. %s", error)
            self.current_key = DEFAULTS["snap_key"]
            self.restore_key = DEFAULTS["restore_key"]
            settings.snap_key = self.current_key
            settings.restore_key = self.restore_key
        self._bound_scan_codes = _scan_codes(self.current_key, self.restore_key)
        # One hook per key: keyboard.hook_key indexes its bookkeeping by key
        # name, so separate press and release hooks on one key cannot both be
        # removed, and the second one would leak.
        self._key_hook = keyboard.hook_key(self.current_key, self._on_key_event)
        self._chord_hook = keyboard.hook(self._on_any_key_event)

    def cleanup(self) -> None:
        """Remove the global keyboard hooks and clear held-key state.

        Quitting runs this from the window and again from the application's
        teardown, so a second call must not unhook the same hooks again.
        """
        for hook in (self._key_hook, self._chord_hook):
            if hook is not None:
                _unhook(hook)
        self._key_hook = None
        self._chord_hook = None
        self._held = False

    def update_keys(self, snap_key: str, restore_key: str) -> bool:
        """Atomically install a distinct snap and restore key pair."""
        try:
            new_snap, new_restore = validate_key_pair(snap_key, restore_key)
        except ValueError as error:
            LOG.warning("Rejecting invalid key bindings. %s", error)
            return False

        if new_snap == self.current_key:
            self.restore_key = new_restore
            self._bound_scan_codes = _scan_codes(new_snap, new_restore)
            return True

        # Install the new hook before removing the old one so a malformed key
        # name cannot leave snapping dead with no working hook installed.
        try:
            new_hook = keyboard.hook_key(new_snap, self._on_key_event)
        except Exception:
            LOG.exception(
                "Rebinding the snap key to %r failed; keeping the current binding.", new_snap
            )
            return False
        _unhook(self._key_hook)
        # The old release hook is gone; a key physically held through the swap
        # would otherwise leave ``_held`` stuck at ``True`` forever.
        self._held = False
        self.current_key = new_snap
        self.restore_key = new_restore
        self._bound_scan_codes = _scan_codes(new_snap, new_restore)
        self._key_hook = new_hook
        with self._press_lock:
            self._press_times = deque(
                self._press_times,
                maxlen=normalize_snap_presses(self.settings.snap_presses),
            )
        return True

    def _on_key_event(self, event):
        """Route one event of the snap key to the press or release handler."""
        if event.event_type == keyboard.KEY_DOWN:
            self._on_press(event)
        else:
            self._on_release(event)

    def _on_any_key_event(self, event):
        """Break a press sequence when another key goes down.

        A snap key used as a modifier, as Shift is while typing capitals, is
        part of a chord rather than a snap gesture. The restore key is
        exempt because holding it while tapping is the restore gesture.
        """
        if event.event_type != keyboard.KEY_DOWN:
            return
        if event.scan_code in self._bound_scan_codes:
            return
        with self._press_lock:
            self._press_times.clear()

    def _on_press(self, event):
        if self._capture_guard is not None and self._capture_guard.is_active:
            return
        if not self.settings.enable_snap:
            return
        now = time.monotonic()
        # Recover from a lost release event (focus steal, UAC prompt, session
        # switch). A genuinely held key produces auto-repeat press events well
        # under a second apart, so a long-silent "held" state is stale.
        if self._held and now - self._last_press_event > 1.0:
            self._held = False
        self._last_press_event = now
        if not self._held:
            self._held = True
            interval = int(self.settings.snap_interval) / 1000.0
            press_target = normalize_snap_presses(self.settings.snap_presses)
            should_trigger = False
            restore = False
            with self._press_lock:
                self._press_times = deque(
                    [t for t in self._press_times if now - t <= interval],
                    maxlen=press_target,
                )
                self._press_times.append(now)
                if len(self._press_times) >= press_target:
                    self._press_times.clear()
                    restore = keyboard.is_pressed(self.restore_key)
                    should_trigger = True
            if should_trigger:
                self.triggered.emit(restore)

    def _on_release(self, event):
        self._held = False


# ------------------------------------------------------------------------------
# Snap and restore.
# ------------------------------------------------------------------------------


class SnapRestoreController(QtCore.QObject):
    """Perform window snap and restore operations."""

    blocked = QtCore.Signal(str)
    # Emitted when the snap gesture targets Fenestra's own fixed-size window,
    # which the window answers by centering itself on its monitor.
    center_requested = QtCore.Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        # Original geometry is captured at snap time (in _snap), not at
        # startup. Restore centers the window at its captured size, so the
        # captured size must be the one the window had right before the user
        # snapped it, not whatever it was when Fenestra launched.
        self._orig_sizes: dict[int, dict[str, object]] = {}

    def _prune_closed_windows(self):
        existing = set()

        def enum_cb(hwnd, _):
            existing.add(hwnd)
            return True

        win32gui.EnumWindows(enum_cb, None)
        stale = []
        for hwnd, original in self._orig_sizes.items():
            if hwnd not in existing:
                stale.append(hwnd)
                continue
            if not _window_identity_matches(hwnd, original):
                stale.append(hwnd)
        for hwnd in stale:
            self._orig_sizes.pop(hwnd, None)

    # Shell windows that must never be moved or resized.
    _SHELL_CLASSES = frozenset(
        {
            "Shell_TrayWnd",
            "Shell_SecondaryTrayWnd",
            "Progman",
            "WorkerW",
            "NotifyIconOverflowWindow",
        }
    )

    @QtCore.Slot(bool)
    def perform(self, restore: bool) -> bool:
        """Snap or restore the foreground window and report whether it acted."""
        self._prune_closed_windows()
        hwnd = USER32.GetForegroundWindow()
        if not hwnd:
            return False
        try:
            if win32gui.GetClassName(hwnd) in self._SHELL_CLASSES:
                LOG.debug("Snap: skipping shell window hwnd=%s.", hwnd)
                return False
        except Exception:
            pass
        try:
            if hwnd in self._fenestra_window_handles():
                if restore:
                    LOG.debug("Restore: Fenestra's own window keeps its fixed size.")
                    return False
                self.center_requested.emit()
                return True
            if restore:
                return bool(self._restore(hwnd))
            return bool(self._snap(hwnd))
        except Exception as e:
            LOG.exception("SnapRestoreController.perform failed.", exc_info=e)
            return False

    @staticmethod
    def _fenestra_window_handles() -> set[int]:
        """Return every live Qt top-level HWND owned by Fenestra.

        ``winId`` creates a native window for a widget that has none, and Qt
        then makes that widget's siblings native too. For the tray menu this
        turned the web view into a child window that swallowed every title-bar
        hit test, so only existing handles are read here.
        """
        from PySide6 import QtWidgets

        handles = set()
        for widget in QtWidgets.QApplication.topLevelWidgets():
            try:
                handle = int(widget.internalWinId() or 0)
            except Exception:
                continue
            if handle:
                handles.add(handle)
        return handles

    def _is_eligible_test_target(self, hwnd: int, fenestra_handles: set[int]) -> bool:
        """Return whether a z-order candidate is safe for an explicit test snap."""
        if not hwnd or hwnd in fenestra_handles:
            return False
        try:
            if win32gui.GetParent(hwnd):
                return False
            if not str(win32gui.GetWindowText(hwnd) or "").strip():
                return False
            if win32gui.GetClassName(hwnd) in self._SHELL_CLASSES:
                return False
        except Exception:
            return False
        return bool(_is_window_interactive(hwnd)) and not _is_window_hidden_from_user(hwnd)

    def find_test_snap_target(self) -> int | None:
        """Return the first eligible non-Fenestra window in foreground z-order."""
        fenestra_handles = self._fenestra_window_handles()
        try:
            hwnd = handle_value(USER32.GetForegroundWindow())
        except Exception:
            return None
        visited: set[int] = set()
        for _ in range(_TEST_TARGET_SCAN_LIMIT):
            if not hwnd or hwnd in visited:
                return None
            visited.add(hwnd)
            if self._is_eligible_test_target(hwnd, fenestra_handles):
                return hwnd
            try:
                hwnd = int(win32gui.GetWindow(hwnd, win32con.GW_HWNDNEXT) or 0)
            except Exception:
                return None
        return None

    def snap_window_for_test(self, hwnd: int) -> bool:
        """Snap one explicitly selected external window after revalidating it."""
        self._prune_closed_windows()
        if not self._is_eligible_test_target(hwnd, self._fenestra_window_handles()):
            return False
        try:
            return bool(self._snap(hwnd))
        except Exception as error:
            LOG.exception("The explicit test snap failed.", exc_info=error)
            return False

    def _is_responsive(self, hwnd: int) -> bool:
        """Return whether a window can be moved without blocking the caller.

        MoveWindow and ShowWindow on another thread's window wait for that
        thread to process messages, so a hung window would freeze Fenestra.
        """
        if not USER32.IsHungAppWindow(hwnd):
            return True
        LOG.info("Skipping hwnd=%s because it is not responding.", hwnd)
        self.blocked.emit("The window is not responding, so it was not moved.")
        return False

    def _snap(self, hwnd: int) -> bool:
        if hwnd in self._fenestra_window_handles():
            LOG.debug("Snap: skipping Fenestra's own window hwnd=%s.", hwnd)
            return False
        if win32gui.IsIconic(hwnd):
            LOG.debug("Snap: skipping minimized window hwnd=%s.", hwnd)
            return False
        if not self._is_responsive(hwnd):
            return False

        def refresh_rect():
            rect = wintypes.RECT()
            USER32.GetWindowRect(hwnd, ctypes.byref(rect))
            return rect

        rc = refresh_rect()
        current_identity = _window_identity(hwnd)
        cached = self._orig_sizes.get(hwnd)
        if cached is not None and not _window_identity_matches(hwnd, cached):
            LOG.info("Snap: HWND %s was reused; discarding stale restore geometry.", hwnd)
            self._orig_sizes.pop(hwnd, None)
            cached = None
        current_rect = (rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)
        if cached is not None:
            last_applied = cached.get("snapped_rect")
            if last_applied is not None and tuple(last_applied) != current_rect:
                _remove_window_identity_token(hwnd, cached)
                self._orig_sizes.pop(hwnd, None)
                cached = None
        pending_record = None
        if hwnd not in self._orig_sizes:
            placement = win32gui.GetWindowPlacement(hwnd)
            was_maximized = placement[1] == win32con.SW_MAXIMIZE
            pending_record = {
                "rect": current_rect,
                "maximized": was_maximized,
                "identity": current_identity,
            }

        def remember_applied_rect(x: int, y: int, width: int, height: int) -> None:
            saved = self._orig_sizes.get(hwnd)
            if saved is not None:
                saved["snapped_rect"] = (int(x), int(y), int(width), int(height))

        record_installed = False

        def ensure_restore_record() -> None:
            nonlocal record_installed
            if pending_record is None or record_installed:
                return
            pending_record["identity_token"] = _install_window_identity_token(hwnd)
            self._orig_sizes[hwnd] = pending_record
            record_installed = True

        def rollback_unapplied_record() -> None:
            nonlocal record_installed
            if not record_installed or pending_record is None:
                return
            _remove_window_identity_token(hwnd, pending_record)
            self._orig_sizes.pop(hwnd, None)
            record_installed = False

        # Get full monitor bounds for accurate fullscreen detection.
        mon_full = get_monitor_rect(hwnd, use_work_area=False)
        if not mon_full:
            return False

        # Check if window is fullscreen using full monitor bounds. Let the
        # helper fetch the DWM extended frame itself: the raw window rect
        # includes invisible borders that defeat the tolerance check.
        full_screen = _is_window_fullscreen(hwnd, monitor_rect=mon_full)

        # Skip a borderless fullscreen window when game mode is enabled.
        if _should_skip_snap_for_game(hwnd, self.settings, full_screen):
            LOG.info("Game mode: skipped snap for fullscreen window hwnd=%s.", hwnd)
            self.blocked.emit("Game mode: fullscreen window was not moved.")
            return False

        # Get the work area for normal snap sizing.
        mon = get_monitor_rect(hwnd, use_work_area=True)
        if not mon:
            return False
        left_edge, top_edge, right_edge, bottom_edge = [int(x) for x in mon]
        monitor_width = int(right_edge - left_edge)
        monitor_height = int(bottom_edge - top_edge)

        # Restore a non-game fullscreen window before moving it.
        pre_move_mutated = False
        if full_screen:
            ensure_restore_record()
            _exit_fullscreen(hwnd)
            pre_move_mutated = True
            rc = refresh_rect()

        placement = win32gui.GetWindowPlacement(hwnd)
        if placement[1] == win32con.SW_MAXIMIZE:
            ensure_restore_record()
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            pre_move_mutated = True
            rc = refresh_rect()

        # Fullscreen and maximized applications can restore their resizable
        # frame only after leaving that state, so classify the current style.
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        process_name = _window_process_name(hwnd)
        center_only = should_center_without_resize(style, process_name)

        border_deltas = window_border_deltas(
            (rc.left, rc.top, rc.right, rc.bottom), _get_window_dwm_rect(hwnd)
        )
        current_width = int(rc.right - rc.left)
        current_height = int(rc.bottom - rc.top)

        if center_only:
            x, y = calculate_center_position(
                left_edge,
                top_edge,
                monitor_width,
                monitor_height,
                current_width,
                current_height,
                border_deltas,
            )
            if (rc.left, rc.top) == (x, y):
                remember_applied_rect(x, y, current_width, current_height)
                return True
            reason = process_name or "non-resizable window"
            LOG.info("Snap: centering %s without resizing hwnd=%s.", reason, hwnd)
            ensure_restore_record()
            try:
                moved = bool(
                    USER32.MoveWindow(
                        hwnd,
                        int(x),
                        int(y),
                        current_width,
                        current_height,
                        True,
                    )
                )
            except Exception:
                if not pre_move_mutated:
                    rollback_unapplied_record()
                raise
            if moved:
                remember_applied_rect(x, y, current_width, current_height)
            elif not pre_move_mutated:
                rollback_unapplied_record()
            return moved

        # Every window that is not resizable returned above. Center the visible
        # frame by sizing the visible box to the requested percentages, then
        # widen the window rect by the invisible DWM borders and shift left/up
        # so the visible frame lands centered.
        x, y, w, h = calculate_snap_position(
            left_edge,
            top_edge,
            monitor_width,
            monitor_height,
            int(self.settings.width_pct),
            int(self.settings.height_pct),
            border_deltas,
        )
        if (rc.right - rc.left, rc.bottom - rc.top, rc.left, rc.top) == (w, h, x, y):
            remember_applied_rect(x, y, w, h)
            return True  # Already at the target; nothing to do.
        ensure_restore_record()
        try:
            moved_to_target = bool(USER32.MoveWindow(hwnd, int(x), int(y), int(w), int(h), True))
        except Exception:
            if not pre_move_mutated:
                rollback_unapplied_record()
            raise
        if not moved_to_target:
            if not pre_move_mutated:
                rollback_unapplied_record()
            return False

        # Some windows advertise WS_SIZEBOX but reject or distort external
        # resize requests. Restore their original size and center it rather
        # than leaving them in a partially resized state.
        moved = refresh_rect()
        actual_width = int(moved.right - moved.left)
        actual_height = int(moved.bottom - moved.top)
        if abs(actual_width - w) > 2 or abs(actual_height - h) > 2:
            fallback_x, fallback_y = calculate_center_position(
                left_edge,
                top_edge,
                monitor_width,
                monitor_height,
                current_width,
                current_height,
                border_deltas,
            )
            LOG.warning(
                "Snap: hwnd=%s did not accept target size %sx%s; "
                "restoring %sx%s and centering only.",
                hwnd,
                w,
                h,
                current_width,
                current_height,
            )
            centered = bool(
                USER32.MoveWindow(
                    hwnd,
                    int(fallback_x),
                    int(fallback_y),
                    current_width,
                    current_height,
                    True,
                )
            )
            if centered:
                remember_applied_rect(
                    fallback_x,
                    fallback_y,
                    current_width,
                    current_height,
                )
            return centered
        remember_applied_rect(moved.left, moved.top, actual_width, actual_height)
        return True

    def _restore(self, hwnd: int) -> bool:
        if hwnd in self._fenestra_window_handles():
            LOG.debug("Restore: skipping Fenestra's own window hwnd=%s.", hwnd)
            return False
        # Peek, do not pop yet: if we cannot resolve a monitor we keep the
        # saved geometry so a later restore can still succeed.
        orig = self._orig_sizes.get(hwnd)
        if not orig:
            return False
        if not _window_identity_matches(hwnd, orig):
            LOG.info("Restore: HWND %s was reused; discarding stale geometry.", hwnd)
            self._orig_sizes.pop(hwnd, None)
            return False
        if not self._is_responsive(hwnd):
            return False
        mon = get_monitor_rect(hwnd)
        if not mon:
            return False
        was_maximized = orig.get("maximized", False)
        rect = orig["rect"]
        left_edge, top_edge, right_edge, bottom_edge = mon

        if was_maximized:
            win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        else:
            _, _, width, height = rect
            # A window maximized or minimized since the snap must leave that
            # state first; otherwise it keeps WS_MAXIMIZE at the new size and
            # the border measurement comes from the wrong frame.
            if win32gui.GetWindowPlacement(hwnd)[1] in (
                win32con.SW_SHOWMINIMIZED,
                win32con.SW_SHOWMAXIMIZED,
            ):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            # Restore the captured size but always center the window on its
            # current monitor, matching snap's DWM-aware centering.
            current = wintypes.RECT()
            USER32.GetWindowRect(hwnd, ctypes.byref(current))
            border_deltas = window_border_deltas(
                (current.left, current.top, current.right, current.bottom),
                _get_window_dwm_rect(hwnd),
            )
            x, y = calculate_center_position(
                left_edge,
                top_edge,
                int(right_edge - left_edge),
                int(bottom_edge - top_edge),
                int(width),
                int(height),
                border_deltas,
            )
            if not USER32.MoveWindow(hwnd, int(x), int(y), int(width), int(height), True):
                return False
        # Restore succeeded: forget the saved geometry so a re-snap captures
        # the new pre-snap position.
        _remove_window_identity_token(hwnd, orig)
        self._orig_sizes.pop(hwnd, None)
        return True


class SnapService:
    """Narrow API surface for snap/restore actions."""

    def __init__(self, shift_mgr):
        """Accept a SnapRestoreController instance (or None during early init)."""
        self._mgr = shift_mgr

    def set_manager(self, mgr):
        """Set or replace the SnapRestoreController instance."""
        self._mgr = mgr

    def test_snap(self) -> dict:
        """Snap the first safe external window behind the settings window."""
        if self._mgr is None:
            return {"ok": False, "error": "The snap manager is not initialized."}
        try:
            target = self._mgr.find_test_snap_target()
            if target is None:
                return {
                    "ok": False,
                    "error": (
                        "No window is eligible behind Fenestra. "
                        "Open or restore another window, then try again."
                    ),
                }
            acted = self._mgr.snap_window_for_test(target)
            if acted:
                return {"ok": True, "message": "Snapped the window behind Fenestra."}
            return {"ok": False, "error": "The selected window could not be snapped."}
        except Exception as e:
            LOG.exception("The snap test failed.")
            return {"ok": False, "error": str(e)}
