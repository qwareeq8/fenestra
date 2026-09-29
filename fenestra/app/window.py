"""MainWindow: frameless, tray-integrated window hosting the React frontend."""

import ctypes
import ctypes.wintypes
import logging
import os
import subprocess
import sys

from PySide6 import QtCore, QtGui, QtWidgets

from fenestra.app.config import (
    APP_NAME,
    DEFAULTS,
    INSTANCE_ACTIVATE_MESSAGE,
    INSTANCE_WINDOW_PROPERTY,
    normalize_snap_presses,
)
from fenestra.app.webview import FenestraWebView
from fenestra.app.window_hit_test import (
    HTCAPTION,
    TITLE_BAR_CONTROLS_WIDTH,
    TITLE_BAR_HEIGHT,
    TITLE_BAR_INTERACTIVE_WIDTH,
    classify_physical_window_hit,
    fixed_window_size,
    normalize_hit_test_regions,
)
from fenestra.bridge import CaptureGuard, FenestraBridge
from fenestra.bridge.bridge import STATUS_ERROR, STATUS_NEUTRAL, STATUS_SUCCESS
from fenestra.platform.resources import resource_path
from fenestra.platform.startup import (
    launch_command,
    main_module_name,
    register_startup_task,
    remove_startup_task,
)
from fenestra.platform.theme import (
    get_windows_theme,
    normalize_theme_mode,
    resolve_theme,
    toggle_theme_mode,
)
from fenestra.platform.win32_abi import USER32
from fenestra.services.explorer_service import ExplorerService
from fenestra.services.snap import MultiPressHotkeyListener, SnapRestoreController, SnapService
from fenestra.settings import Settings, SettingsState
from fenestra.workers.key_capture import KeyCaptureWorker

LOG = logging.getLogger("Fenestra")

# ChangeWindowMessageFilterEx action that admits a message from a process at a
# lower integrity level.
_MSGFLT_ALLOW = 1

# After a display scale change, the window centers itself once Windows has
# stopped moving and resizing it for this long.
RECENTER_SETTLE_MS = 300

_WM_NCLBUTTONDBLCLK = 0x00A3
_WM_ENTERSIZEMOVE = 0x0231
_WM_EXITSIZEMOVE = 0x0232
_WM_SYSCOMMAND = 0x0112
_SC_MAXIMIZE = 0xF030

# ------------------------------------------------------------------------------
# Autostart.
# ------------------------------------------------------------------------------


def sync_startup_task(enabled: bool) -> None:
    """Make elevated autostart match the persisted run-at-startup setting.

    Autostart uses a highest-run-level logon task, the mechanism PowerToys
    uses, so sign-in starts Fenestra elevated without a UAC prompt. A
    Startup-folder shortcut cannot do that for a self-elevating application.
    """
    if enabled:
        frozen = bool(getattr(sys, "frozen", False))
        module = main_module_name()
        target, arguments = launch_command(
            sys.executable, sys.argv[:1], frozen=frozen, main_module=module
        )
        if frozen:
            working_directory = os.path.dirname(target)
        elif module:
            # The package must stay importable without its own directory
            # leading sys.path.
            working_directory = os.getcwd()
        else:
            working_directory = os.path.dirname(arguments[0])
        register_startup_task(target, subprocess.list2cmdline(arguments), working_directory)
    else:
        remove_startup_task()


# ------------------------------------------------------------------------------
# Main window with tray icon.
# ------------------------------------------------------------------------------


class MainWindow(QtWidgets.QMainWindow):
    """Main application window with tray icon and QWebEngineView frontend.

    The React frontend renders inside ``QWebEngineView``. ``FenestraBridge``
    mediates settings, theme, capture, and snap communication. The window has
    a fixed size of 1000 by 620, reduced to fit a smaller screen but never
    below 860 by 600. ``WM_NCHITTEST`` lets its title bar drag it, and it
    cannot be maximized.
    """

    snap_key_status = QtCore.Signal(str, int, str)

    def __init__(self):
        super().__init__()
        self.settings = Settings()
        self.settings.snap_key = str(self.settings.snap_key)
        self.settings.restore_key = str(self.settings.restore_key)
        self.settings.enable_snap = bool(self.settings.enable_snap)
        self.settings.snap_presses = normalize_snap_presses(self.settings.snap_presses)
        self.settings.snap_interval = int(self.settings.snap_interval)
        self.settings.width_pct = int(self.settings.width_pct)
        self.settings.height_pct = int(self.settings.height_pct)
        self.settings.ex_auto_size = bool(getattr(self.settings, "ex_auto_size", False))
        self.settings.game_mode_enabled = bool(self.settings.game_mode_enabled)
        self.settings.run_at_startup = bool(self.settings.run_at_startup)
        self.settings.theme = normalize_theme_mode(str(self.settings.theme), DEFAULTS["theme"])

        self._capture_guard = CaptureGuard()
        self._capture_thread = None
        self._capture_worker = None
        self._capture_target = None
        self._frontend_shortcuts_enabled = True
        self._shutdown_started = False

        self._hit_test_interactive_width = TITLE_BAR_INTERACTIVE_WIDTH
        self._hit_test_controls_width = TITLE_BAR_CONTROLS_WIDTH
        self._hit_test_title_bar_height = TITLE_BAR_HEIGHT

        self.is_first_show = True

        # A second launch posts this message to ask the window to show itself.
        try:
            self._activate_message = int(USER32.RegisterWindowMessageW(INSTANCE_ACTIVATE_MESSAGE))
        except Exception:
            LOG.exception("Could not register the single-instance activation message.")
            self._activate_message = 0

        self._theme_mode = self.settings.theme
        self._theme_state = self.settings.theme

        # React to OS theme changes instead of polling the registry. Qt tracks
        # the Windows "apps use light theme" setting and emits on change.
        QtGui.QGuiApplication.styleHints().colorSchemeChanged.connect(self._sync_system_theme)

        self.setWindowTitle(APP_NAME)
        icon_path = resource_path("icon.ico")
        if os.path.exists(icon_path):
            icon = QtGui.QIcon(icon_path)
        else:
            icon = QtGui.QIcon.fromTheme("applications-system")
        self.setWindowIcon(icon)
        QtWidgets.QApplication.setWindowIcon(icon)

        # A frameless window with a fixed size that moves by its title bar.
        self.setWindowFlag(QtCore.Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(QtCore.Qt.WindowType.WindowMaximizeButtonHint, False)
        self._scale_key = None
        self._in_move_loop = False
        self._recenter_pending = False
        self._recenter_timer = QtCore.QTimer(self)
        self._recenter_timer.setSingleShot(True)
        self._recenter_timer.setInterval(RECENTER_SETTLE_MS)
        self._recenter_timer.timeout.connect(self._finish_pending_recenter)
        self._fit_to_screen(QtWidgets.QApplication.primaryScreen())

        self.tray_icon = QtWidgets.QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("Fenestra")
        menu = QtWidgets.QMenu(self)
        open_act = menu.addAction("Open")
        open_act.triggered.connect(self._restore_window)

        self.minimize_to_tray_on_exit = bool(getattr(self.settings, "minimize_to_tray", True))
        self.action_minimize_on_exit = menu.addAction("Minimize to Tray")
        self.action_minimize_on_exit.setCheckable(True)
        self.action_minimize_on_exit.setChecked(self.minimize_to_tray_on_exit)
        self.action_minimize_on_exit.triggered.connect(self._toggle_minimize_on_exit)

        self.action_run_at_startup = menu.addAction("Run at Startup")
        self.action_run_at_startup.setCheckable(True)
        self.action_run_at_startup.setChecked(bool(self.settings.run_at_startup))
        self.action_run_at_startup.triggered.connect(self._toggle_run_at_startup)

        exit_act = menu.addAction("Quit")
        exit_act.triggered.connect(self._really_quit)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

        # --- Bridge + WebView ---
        self._settings_state = SettingsState(self.settings)
        self._snap_service = SnapService(None)  # ``shift_mgr`` is set after construction.
        self._bridge = FenestraBridge(self._settings_state, self._snap_service, parent=self)
        self._bridge.set_main_window(self)
        self._bridge.set_capture_guard(self._capture_guard)

        self.webview = FenestraWebView(self._bridge, parent=self)

        # React handles all UI, so the web view is the central widget.
        self.setCentralWidget(self.webview)

        # Route the ``snap_key_status`` signal to the bridge.
        self.snap_key_status.connect(self._bridge.snap_status.emit)

        # Folder view tasks restart Explorer; bring the autosize worker back
        # afterward (queued from the task's worker thread to the GUI thread).
        self._bridge.explorer_service_restart.connect(self._update_explorer_autosize_thread)

        # Register window shortcuts.
        self._window_shortcuts = [
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+T"), self, activated=self._toggle_theme),
            QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Enter"), self, activated=self._test_snap),
            QtGui.QShortcut(QtGui.QKeySequence("F1"), self, activated=self._show_help),
        ]

        # The listener counts presses on its hook thread; the controller moves
        # windows on the GUI thread through the queued triggered signal.
        self._hotkey_listener = MultiPressHotkeyListener(
            self.settings, capture_guard=self._capture_guard
        )
        self.shift_mgr = SnapRestoreController(self.settings)
        self._hotkey_listener.triggered.connect(self.shift_mgr.perform)
        self.shift_mgr.center_requested.connect(self._center_on_current_screen)
        self.shift_mgr.blocked.connect(
            lambda message: self.snap_key_status.emit(message, 3000, STATUS_NEUTRAL)
        )
        self._snap_service.set_manager(self.shift_mgr)

        self._explorer_service = ExplorerService(self.settings, parent=self)
        self._update_explorer_autosize_thread()

        self._apply_theme_mode(self._theme_mode)
        QtCore.QTimer.singleShot(0, self._reconcile_startup_task)

    # ------------------------------------------------------------------
    # Tray behavior
    # ------------------------------------------------------------------

    def changeEvent(self, event):
        """Hide to the tray instead of minimizing when that setting is on."""
        self._track_scale()
        if event.type() == QtCore.QEvent.Type.WindowStateChange:
            if self.isMinimized() and self.minimize_to_tray_on_exit:
                QtCore.QTimer.singleShot(0, self.hide)
        super().changeEvent(event)

    def closeEvent(self, event):
        """Hide to the tray, or stop background work and quit."""
        if self.minimize_to_tray_on_exit:
            event.ignore()
            self.hide()
        else:
            self._stop_background_threads()
            self._hotkey_listener.cleanup()
            QtWidgets.QApplication.quit()

    def _on_tray_activated(self, reason):
        if reason in (
            QtWidgets.QSystemTrayIcon.ActivationReason.Trigger,
            QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._restore_window()

    def _restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _really_quit(self):
        self._stop_background_threads()
        self._hotkey_listener.cleanup()
        QtWidgets.QApplication.quit()

    def _stop_background_threads(self):
        if self._shutdown_started:
            return
        self._shutdown_started = True
        # Let any in-flight folder view registry task finish so it is never
        # killed mid-write during shutdown.
        try:
            self._bridge.wait_for_view_task()
        except Exception:
            LOG.exception("Waiting for the folder-view task failed.")
        self._stop_capture_worker()
        self._explorer_service.stop()

    # ------------------------------------------------------------------
    # Key capture uses bridge signals for status updates.
    # ------------------------------------------------------------------

    def _start_key_capture(self) -> str | None:
        return self._begin_key_capture("snap", "Press the desired snap key. Press Esc to cancel.")

    def _start_restore_key_capture(self) -> str | None:
        return self._begin_key_capture(
            "restore", "Press the desired restore key. Press Esc to cancel."
        )

    def _begin_key_capture(self, target: str, message: str) -> str | None:
        """Start a key-capture session.

        Returns None when capture began, or the reason it could not begin.
        """
        if not self._capture_guard.try_start():
            error = "Key capture is already in progress."
            self._bridge.snap_status.emit(error, 2000, STATUS_ERROR)
            return error
        self._refresh_window_shortcuts()
        try:
            self._capture_target = target
            self._capture_thread = QtCore.QThread(self)
            self._capture_worker = KeyCaptureWorker()
        except Exception:
            # Construction failed; release the guard or capture is dead forever.
            self._capture_guard.finish()
            self._capture_target = None
            self._capture_thread = None
            self._capture_worker = None
            self._refresh_window_shortcuts()
            LOG.exception("Key-capture worker construction failed.")
            error = "Key capture could not start."
            self._bridge.snap_status.emit(error, 3000, STATUS_ERROR)
            return error
        self._bridge.snap_status.emit(message, 0, STATUS_NEUTRAL)
        self._bridge.capture_status.emit("capturing")
        self._capture_worker.moveToThread(self._capture_thread)
        self._capture_thread.started.connect(self._capture_worker.run)
        self._capture_worker.captured.connect(self._on_capture_key)
        self._capture_worker.cancelled.connect(self._on_capture_cancelled)
        self._capture_worker.finished.connect(self._capture_thread.quit)
        self._capture_worker.finished.connect(self._capture_worker.deleteLater)
        thread = self._capture_thread
        self._capture_thread.finished.connect(self._capture_thread.deleteLater)
        self._capture_thread.finished.connect(
            lambda thread=thread: self._on_capture_finished(thread)
        )
        self._capture_thread.start()
        return None

    def _on_capture_key(self, key: str) -> None:
        key_str = str(key).lower()
        target_key = "restore_key" if self._capture_target == "restore" else "snap_key"
        result = self._settings_state.apply_draft({target_key: key_str})
        if not result.get("ok"):
            self._bridge.capture_status.emit("cancelled")
            self._bridge.snap_status.emit(
                result.get("error", "Key binding was not changed."), 5000, STATUS_ERROR
            )
            return
        self._bridge.publish_settings()
        self._bridge.dirty_changed.emit(True)
        self._bridge.capture_status.emit("done")
        label = "Restore" if self._capture_target == "restore" else "Snap"
        self._bridge.snap_status.emit(
            f"{label} key set to {key_str.upper()}.", 3000, STATUS_SUCCESS
        )

    def _on_capture_cancelled(self, reason: str) -> None:
        message = "Key capture timed out." if reason == "timeout" else "Key capture cancelled."
        self._bridge.capture_status.emit("cancelled" if reason != "timeout" else "timeout")
        self._bridge.snap_status.emit(message, 2000, STATUS_NEUTRAL)

    def _cancel_key_capture(self) -> None:
        """Stop an in-progress capture so the global keyboard hook is released.

        Signals the worker to stop; the normal cancelled/finished path then
        emits capture_status and releases the guard.
        """
        worker = getattr(self, "_capture_worker", None)
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                LOG.exception("Cancelling key capture failed.")

    def _on_capture_finished(self, finished_thread=None) -> None:
        if finished_thread is not None and finished_thread is not self._capture_thread:
            return
        if self._capture_thread is not None and self._capture_thread.isRunning():
            return
        self._capture_guard.finish()
        self._capture_thread = None
        self._capture_worker = None
        self._capture_target = None
        self._refresh_window_shortcuts()

    def _stop_capture_worker(self) -> None:
        worker = getattr(self, "_capture_worker", None)
        thread = getattr(self, "_capture_thread", None)
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                pass
        if thread is not None:
            thread.quit()
            if not thread.wait(2000):
                LOG.warning("Key capture: the thread did not stop in time; keeping its reference.")
                return
            self._on_capture_finished(thread)
            return
        self._on_capture_finished()

    # ------------------------------------------------------------------
    # Business logic actions.
    # ------------------------------------------------------------------

    def _test_snap(self) -> None:
        if self._capture_guard.is_active:
            return
        try:
            result = self._snap_service.test_snap()
            message = result.get("message", result.get("error", "Could not test snapping."))
            tone = STATUS_SUCCESS if result.get("ok") else STATUS_ERROR
            self._bridge.snap_status.emit(message, 2000, tone)
        except Exception:
            LOG.exception("Testing a snap on Fenestra failed.")
            self._bridge.snap_status.emit("Could not test snapping.", 2000, STATUS_ERROR)

    def _set_modal_shortcuts_enabled(self, enabled: bool) -> None:
        """Enable native shortcuts only when no frontend confirmation is modal."""
        self._frontend_shortcuts_enabled = bool(enabled)
        self._refresh_window_shortcuts()

    def _refresh_window_shortcuts(self) -> None:
        """Keep native shortcuts disabled during a modal or key capture."""
        enabled = self._frontend_shortcuts_enabled and not self._capture_guard.is_active
        for shortcut in self._window_shortcuts:
            shortcut.setEnabled(enabled)

    def _show_help(self):
        """Show a summary of the gestures and in-app shortcuts."""
        QtWidgets.QMessageBox.information(
            self,
            "Fenestra Help",
            "Snap: tap the snap key the set number of times within the interval to "
            "resize the foreground window and center it.\n\n"
            "Restore: hold the restore key while tapping the snap key to return the "
            "window to its size before the snap, centered.\n\n"
            "Size: the snapped width and height are percentages of the current monitor.\n\n"
            "Explorer: autosizing fits Details view columns each time a folder opens.\n\n"
            "Game mode: fullscreen windows, which are usually games, are left alone.\n\n"
            "Shortcuts: Ctrl+K opens the command palette, Ctrl+S saves, Ctrl+T toggles "
            "the theme, Ctrl+Enter tests a snap, and F1 shows this help.",
        )

    def _update_explorer_autosize_thread(self, *_args) -> None:
        """Start or stop the Explorer autosize worker to match the setting."""
        self._explorer_service.start()

    def showEvent(self, event):
        """Mark the native window for single-instance focus and center it once."""
        super().showEvent(event)
        try:
            hwnd = int(self.winId())
            if not USER32.SetPropW(hwnd, INSTANCE_WINDOW_PROPERTY, 1):
                LOG.warning("Could not mark Fenestra's native window for single-instance focus.")
            # The elevated window must admit the activation message from an
            # unelevated second launch, which UIPI would otherwise drop.
            if self._activate_message and not USER32.ChangeWindowMessageFilterEx(
                hwnd, self._activate_message, _MSGFLT_ALLOW, None
            ):
                LOG.warning("Could not admit the single-instance activation message.")
        except Exception:
            LOG.exception("Could not mark Fenestra's native window for single-instance focus.")
        self._update_explorer_autosize_thread()
        if self.is_first_show:
            self.is_first_show = False
            # Defer centering to the next event-loop turn instead of forcing a
            # reentrant processEvents() inside the show handler.
            QtCore.QTimer.singleShot(0, self.center_on_screen)

    def _toggle_minimize_on_exit(self) -> None:
        checked = self.action_minimize_on_exit.isChecked()
        if not self._persist_immediate_settings({"minimize_to_tray": checked}):
            self.action_minimize_on_exit.setChecked(not checked)

    def _toggle_run_at_startup(self) -> None:
        checked = self.action_run_at_startup.isChecked()
        if not self._persist_immediate_settings({"run_at_startup": checked}):
            self.action_run_at_startup.setChecked(not checked)

    def _toggle_theme(self) -> None:
        new_mode = toggle_theme_mode(self._theme_mode, get_windows_theme())
        if not self._persist_immediate_settings({"theme": new_mode}):
            self._bridge.snap_status.emit("Could not save the theme setting.", 3000, STATUS_ERROR)

    def _persist_immediate_settings(self, data: dict) -> bool:
        """Persist an immediate control without committing the shared draft."""
        try:
            result = self._settings_state.persist_immediate(data)
            if not result.get("ok"):
                return False
            self._bridge._apply_side_effects(result.get("applied", {}))
            self._bridge.dirty_changed.emit(self._settings_state.has_draft)
            self._bridge.publish_settings()
            return True
        except Exception:
            LOG.exception("The immediate settings update failed.")
            return False

    def _reconcile_startup_task(self) -> None:
        """Retry the persisted startup preference on every application launch."""
        try:
            sync_startup_task(bool(self.settings.run_at_startup))
        except Exception:
            LOG.exception("Reconciling the persisted logon task failed.")
            self._bridge.snap_status.emit(
                "The run-at-startup setting is saved, but its logon task could not be updated. "
                "Fenestra will retry at the next launch.",
                8000,
                STATUS_ERROR,
            )

    def _apply_theme_mode(self, mode: str) -> None:
        self._theme_mode = normalize_theme_mode(mode, DEFAULTS["theme"])
        # The colorSchemeChanged connection stays in place; _sync_system_theme
        # ignores changes while an explicit theme is selected.
        if self._theme_mode == "system":
            self._sync_system_theme()
        else:
            self.set_theme(self._theme_mode)

    def _sync_system_theme(self) -> None:
        if self._theme_mode != "system":
            return
        effective = resolve_theme("system", get_windows_theme())
        if effective != self._theme_state:
            self.set_theme(effective)

    def set_theme(self, theme: str) -> None:
        """Publish the effective theme to the frontend."""
        self._theme_state = theme
        self._bridge.theme_applied.emit(theme)

    def center_on_screen(self, screen=None) -> None:
        """Fit the window to a monitor and center it there.

        Parameters
        ----------
        screen : QtGui.QScreen, optional
            The monitor to use. The default is the one containing the pointer.
        """
        if screen is None:
            screen = (
                QtWidgets.QApplication.screenAt(QtGui.QCursor.pos())
                or QtWidgets.QApplication.primaryScreen()
            )
        if screen is None:
            return
        self._fit_to_screen(screen)
        g = screen.availableGeometry()
        w = self.size()
        x = g.x() + (g.width() - w.width()) // 2
        y = g.y() + (g.height() - w.height()) // 2
        self.move(int(x), int(y))

    def _center_on_current_screen(self) -> None:
        """Center the window on the monitor it is on, as a snap would."""
        handle = self.windowHandle()
        self.center_on_screen(handle.screen() if handle is not None else None)

    def _fit_to_screen(self, screen) -> None:
        """Fix the window size to the default, reduced to fit the monitor."""
        width, height = fixed_window_size(screen.availableGeometry() if screen else None)
        self.setFixedSize(width, height)

    def _track_scale(self) -> None:
        """Center the window again after its monitor's scale changes.

        Windows keeps a window's top-left corner when the display scale
        changes, so the resized window drifts toward the bottom right, and
        neither Qt's screen signals nor its pixel-ratio event reliably report
        the change. The window instead compares its own pixel ratio on the
        same monitor whenever it moves, resizes, or changes. A change while
        the user drags it, including onto a monitor with another scale, only
        updates the baseline.
        """
        handle = self.windowHandle()
        if handle is None:
            return
        key = (handle.screen(), self.devicePixelRatioF())
        previous, self._scale_key = self._scale_key, key
        if (
            previous is not None
            and not self._in_move_loop
            and previous[0] is key[0]
            and previous[1] != key[1]
        ):
            # Windows moves and resizes the window for the new scale after
            # reporting it, and that would undo an immediate centering.
            self._recenter_pending = True
        if self._recenter_pending:
            self._recenter_timer.start()

    def _finish_pending_recenter(self) -> None:
        if not self._recenter_pending:
            return
        self._recenter_pending = False
        self._center_on_current_screen()

    def moveEvent(self, event):
        """Track the monitor scale as Windows repositions the window."""
        super().moveEvent(event)
        self._track_scale()

    def resizeEvent(self, event):
        """Track the monitor scale as Windows resizes the window."""
        super().resizeEvent(event)
        self._track_scale()

    # ------------------------------------------------------------------
    # Title-bar dragging through ``WM_NCHITTEST``.
    # ------------------------------------------------------------------

    def set_hit_test_regions(
        self,
        interactive_width: int,
        controls_width: int,
        title_bar_height: int,
    ) -> None:
        """Store frontend-measured chrome regions in device-independent pixels."""
        interactive_width, controls_width, title_bar_height = normalize_hit_test_regions(
            interactive_width,
            controls_width,
            title_bar_height,
        )
        self._hit_test_interactive_width = interactive_width
        self._hit_test_controls_width = controls_width
        self._hit_test_title_bar_height = title_bar_height

    def nativeEvent(self, event_type, message):
        """Answer WM_NCHITTEST and the single-instance activation message."""
        if event_type == b"windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if self._activate_message and msg.message == self._activate_message:
                # Restore through Qt, outside the window procedure, so Qt's
                # visibility state matches the native window.
                QtCore.QTimer.singleShot(0, self._restore_window)
                return True, 0
            # The window has a fixed size, so double-clicking the title bar,
            # Win+Up, and the system menu must not maximize it.
            if msg.message == _WM_NCLBUTTONDBLCLK and msg.wParam == HTCAPTION:
                return True, 0
            if msg.message == _WM_SYSCOMMAND and (msg.wParam & 0xFFF0) == _SC_MAXIMIZE:
                return True, 0
            if msg.message == _WM_ENTERSIZEMOVE:
                self._in_move_loop = True
            elif msg.message == _WM_EXITSIZEMOVE:
                self._in_move_loop = False
                self._track_scale()
            if msg.message == 0x0084:  # WM_NCHITTEST
                x = ctypes.c_short(msg.lParam & 0xFFFF).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                rect = ctypes.wintypes.RECT()
                if USER32.GetWindowRect(msg.hWnd, ctypes.byref(rect)):
                    result = classify_physical_window_hit(
                        x,
                        y,
                        (rect.left, rect.top, rect.right, rect.bottom),
                        self.devicePixelRatioF(),
                        interactive_width=self._hit_test_interactive_width,
                        controls_width=self._hit_test_controls_width,
                        title_bar_height=self._hit_test_title_bar_height,
                    )
                    if result:
                        return True, result

        return super().nativeEvent(event_type, message)
