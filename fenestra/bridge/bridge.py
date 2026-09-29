"""QWebChannel bridge between React frontend and Python backend.

The module exposes one ``FenestraBridge`` QObject through narrow, JSON-based
slots. Every input is validated, and no broad Python object is exposed.

The bridge is registered with QWebChannel as "bridge" so JavaScript accesses
it as channel.objects.bridge.

Slots return ``{"ok": true, "data": ...}`` on success and
``{"ok": false, "error": "..."}`` on failure.
"""

import json
import logging
import threading

from PySide6.QtCore import QObject, Signal, Slot

from fenestra.app.config import DEFAULTS
from fenestra.services.snap import SnapService
from fenestra.settings.state import SettingsState

LOG = logging.getLogger("Fenestra")
_TRANSACTION_KEY = "__fenestraTransaction"
_SAVED_KEY = "__fenestraSaved"

# Tones for snap_status and views_status. The footer styles a message by its
# tone and never infers severity from the wording.
STATUS_NEUTRAL = "neutral"
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"


def _normalize_transaction_id(value) -> str:
    """Return a bounded frontend transaction identifier for signal correlation."""
    if not isinstance(value, str):
        return ""
    return value[:128]


class FenestraBridge(QObject):
    """Narrow JSON bridge between React UI and Python backend.

    Slot methods accept and return JSON strings or primitive values. Signals
    push updates from Python to React.

    Register the bridge with ``QWebChannel.registerObject("bridge", self)``.
    JavaScript then calls ``channel.objects.bridge.get_settings(callback)``.
    """

    # Signals carry backend updates to JavaScript.
    settings_changed = Signal(str)  # This is the JSON string for the complete settings mapping.
    theme_applied = Signal(str)  # This is the effective "dark" or "light" theme.
    snap_status = Signal(str, int, str)  # These are the message, timeout in ms, and tone.
    capture_status = Signal(str)  # This is a capture state such as "capturing" or "done".
    dirty_changed = Signal(bool)  # ``True`` means that an unsaved draft exists.
    views_status = Signal(str, int, str)  # These report a folder-view task the same way.
    explorer_service_restart = Signal()  # This restarts autosizing after Explorer restarts.

    def __init__(
        self,
        settings_state: SettingsState,
        snap_service: SnapService,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._state = settings_state
        self._snap = snap_service

        # ``MainWindow`` sets these references after construction.
        self._main_window = None
        self._capture_guard = None
        self._views_thread: threading.Thread | None = None

    def set_main_window(self, mw) -> None:
        """Set the ``MainWindow`` reference for theme, capture, and startup operations."""
        self._main_window = mw

    def set_capture_guard(self, guard) -> None:
        """Set the ``CaptureGuard`` used to gate key capture."""
        self._capture_guard = guard

    @Slot(bool, result=str)
    def set_modal_open(self, is_open: bool) -> str:
        """Disable native window shortcuts while a web confirmation is modal."""
        try:
            if self._main_window is not None:
                self._main_window._set_modal_shortcuts_enabled(not bool(is_open))
            return json.dumps({"ok": True})
        except Exception as error:
            LOG.exception("Updating the frontend modal state failed.")
            return json.dumps({"ok": False, "error": str(error)})

    def _settings_payload(self) -> dict:
        """Return the draft-overlaid settings with the saved values attached.

        The frontend edits the draft, but some indicators must describe the
        settings in effect, so every payload also carries the saved values.
        """
        settings = self._state.get_all()
        settings[_SAVED_KEY] = self._state.get_saved()
        return settings

    def _settings_json(self, transaction_id: str = "") -> str:
        """Serialize settings and optionally identify the frontend operation that emitted them."""
        settings = self._settings_payload()
        transaction_id = _normalize_transaction_id(transaction_id)
        if transaction_id:
            settings[_TRANSACTION_KEY] = transaction_id
        return json.dumps(settings)

    def publish_settings(self) -> None:
        """Push the current settings after a change the frontend did not request."""
        self.settings_changed.emit(self._settings_json())

    # Settings slots.

    @Slot(result=str)
    def get_settings(self) -> str:
        """Return all settings as a structured JSON payload."""
        try:
            settings = self._settings_payload()
            return json.dumps({"ok": True, "data": settings})
        except Exception as e:
            LOG.exception("Reading settings through the bridge failed.")
            return json.dumps({"ok": False, "error": str(e)})

    @Slot(str, str, result=str)
    def save_settings(self, json_str: str, transaction_id: str = "") -> str:
        """Store a partial settings update in draft (not persisted).

        The input is a JSON object of key-value pairs. Changes are staged in
        the draft model until ``commit_draft`` persists them.
        """
        try:
            data = json.loads(json_str)
            if not isinstance(data, dict):
                return json.dumps({"ok": False, "error": "Expected a JSON object."})
            transaction_id = _normalize_transaction_id(transaction_id)
            result = self._state.apply_draft(data)
            if result.get("ok"):
                # Push settings with the draft overlay to the frontend.
                self.settings_changed.emit(self._settings_json(transaction_id))
                self.dirty_changed.emit(self._state.has_draft)
                if "theme" in data and self._main_window:
                    applied_theme = result.get("applied", {}).get("theme")
                    if applied_theme:
                        self._main_window._apply_theme_mode(applied_theme)
            return json.dumps(result)
        except json.JSONDecodeError as e:
            return json.dumps({"ok": False, "error": f"Invalid JSON: {e}."})
        except Exception as e:
            LOG.exception("Staging settings through the bridge failed.")
            return json.dumps({"ok": False, "error": str(e)})

    @Slot(str, result=str)
    def commit_draft(self, transaction_id: str = "") -> str:
        """Persist draft settings to QSettings and apply side effects."""
        hotkeys_prepared = False
        previous_hotkeys = None
        try:
            candidate = self._state.get_all()
            if self._main_window is not None and hasattr(self._main_window, "_hotkey_listener"):
                previous_hotkeys = (
                    self._state.get_persisted("snap_key"),
                    self._state.get_persisted("restore_key"),
                )
                desired_hotkeys = (candidate["snap_key"], candidate["restore_key"])
                if desired_hotkeys != previous_hotkeys:
                    if not self._main_window._hotkey_listener.update_keys(*desired_hotkeys):
                        return json.dumps(
                            {
                                "ok": False,
                                "error": (
                                    "The new keyboard hooks could not be installed. "
                                    "Your changes remain unsaved."
                                ),
                            }
                        )
                    hotkeys_prepared = True
            # SettingsState.commit_draft either persists the draft and reports
            # success or raises, so the failure path is this handler alone.
            result = self._state.commit_draft()
        except Exception as e:
            if hotkeys_prepared and previous_hotkeys is not None:
                if not self._main_window._hotkey_listener.update_keys(*previous_hotkeys):
                    LOG.critical("Could not restore keyboard hooks after commit failure.")
            LOG.exception("Committing draft settings failed.")
            return json.dumps({"ok": False, "error": str(e)})

        # Persistence is the transaction boundary. Once it succeeds, never roll
        # back the matching hooks because an unrelated UI or service refresh
        # failed. Doing so would leave runtime hooks out of sync with QSettings.
        try:
            self.settings_changed.emit(self._settings_json(transaction_id))
            self.dirty_changed.emit(False)
            self._apply_side_effects(
                result.get("applied", {}), hotkeys_already_applied=hotkeys_prepared
            )
        except Exception:
            LOG.exception("A post-commit settings side effect failed.")
            self.snap_status.emit(
                "Settings were saved, but one runtime update failed. Restart Fenestra to retry.",
                7000,
                STATUS_ERROR,
            )
        return json.dumps(result)

    @Slot(str, result=str)
    def discard_draft(self, transaction_id: str = "") -> str:
        """Discard unsaved changes and push persisted settings to frontend."""
        try:
            self._state.discard_draft()
            self.settings_changed.emit(self._settings_json(transaction_id))
            self.dirty_changed.emit(False)
            if self._main_window:
                persisted_theme = self._state.get_persisted("theme")
                self._main_window._apply_theme_mode(persisted_theme)
            return json.dumps({"ok": True, "data": self._settings_payload()})
        except Exception as e:
            LOG.exception("Discarding draft settings failed.")
            return json.dumps({"ok": False, "error": str(e)})

    @Slot(str, result=str)
    def reset_defaults(self, transaction_id: str = "") -> str:
        """Reset all settings to defaults and return the new structured payload."""
        hotkeys_prepared = False
        previous_hotkeys = None
        try:
            if self._main_window is not None and hasattr(self._main_window, "_hotkey_listener"):
                previous_hotkeys = (
                    self._state.get_persisted("snap_key"),
                    self._state.get_persisted("restore_key"),
                )
                desired_hotkeys = (DEFAULTS["snap_key"], DEFAULTS["restore_key"])
                if desired_hotkeys != previous_hotkeys:
                    if not self._main_window._hotkey_listener.update_keys(*desired_hotkeys):
                        return json.dumps(
                            {
                                "ok": False,
                                "error": (
                                    "The default keyboard hooks could not be installed. "
                                    "Settings were not reset."
                                ),
                            }
                        )
                    hotkeys_prepared = True
            new_settings = self._state.reset_to_defaults()
        except Exception as e:
            if hotkeys_prepared and previous_hotkeys is not None:
                if not self._main_window._hotkey_listener.update_keys(*previous_hotkeys):
                    LOG.critical("Could not restore keyboard hooks after reset failure.")
            LOG.exception("Resetting settings to defaults failed.")
            return json.dumps({"ok": False, "error": str(e)})

        try:
            self.settings_changed.emit(self._settings_json(transaction_id))
            self.dirty_changed.emit(False)
            if self._main_window:
                self._apply_side_effects(new_settings, hotkeys_already_applied=hotkeys_prepared)
        except Exception:
            LOG.exception("A post-reset settings side effect failed.")
            self.snap_status.emit(
                "Defaults were saved, but one runtime update failed. Restart Fenestra to retry.",
                7000,
                STATUS_ERROR,
            )
        return json.dumps({"ok": True, "data": self._settings_payload()})

    # Snap slots.

    @Slot(result=str)
    def test_snap(self) -> str:
        """Snap the first safe external window behind Fenestra in z-order."""
        try:
            result = self._snap.test_snap()
            msg = result.get("message", result.get("error", ""))
            tone = STATUS_SUCCESS if result.get("ok") else STATUS_ERROR
            self.snap_status.emit(msg, 2000, tone)
            return json.dumps(result)
        except Exception as e:
            LOG.exception("Testing a snap through the bridge failed.")
            return json.dumps({"ok": False, "error": str(e)})

    # Key-capture slots.

    @Slot(str, result=str)
    def capture_key(self, target: str) -> str:
        """Start key capture for 'snap' or 'restore' target."""
        if target not in ("snap", "restore"):
            return json.dumps({"ok": False, "error": f"Invalid capture target: {target!r}."})
        if self._main_window is None:
            return json.dumps({"ok": False, "error": "The main window is not ready."})
        try:
            if target == "snap":
                error = self._main_window._start_key_capture()
            else:
                error = self._main_window._start_restore_key_capture()
            # MainWindow emits capture_status("capturing") itself on success.
            if error:
                return json.dumps({"ok": False, "error": error})
            return json.dumps({"ok": True})
        except Exception as e:
            LOG.exception("Starting key capture through the bridge failed.")
            return json.dumps({"ok": False, "error": str(e)})

    @Slot(result=str)
    def cancel_capture(self) -> str:
        """Cancel an in-progress key capture and release the global hook.

        Without this, closing the capture UI (Escape or clicking away) leaves
        the backend hook active until timeout, so the next key pressed in any
        application is silently captured as the new binding.
        """
        if self._main_window is None:
            return json.dumps({"ok": True})
        try:
            self._main_window._cancel_key_capture()
            return json.dumps({"ok": True})
        except Exception as e:
            LOG.exception("Cancelling key capture through the bridge failed.")
            return json.dumps({"ok": False, "error": str(e)})

    # Theme slots.

    @Slot(result=str)
    def get_theme_mode(self) -> str:
        """Return current theme mode and effective theme as structured JSON."""
        mode = "system"
        effective = "dark"
        if self._main_window:
            mode = getattr(self._main_window, "_theme_mode", "system")
            effective = getattr(self._main_window, "_theme_state", "dark")
        return json.dumps({"ok": True, "data": {"mode": mode, "effective": effective}})

    # Explorer default-view slots.

    @Slot(result=str)
    def apply_details_view(self) -> str:
        """Make Details the default view for all folders. Restarts Explorer."""
        return self._start_view_task("apply", "Details is now the default view for all folders.")

    @Slot(result=str)
    def reset_folder_views(self) -> str:
        """Reset all folder views to Windows defaults. Restarts Explorer."""
        return self._start_view_task("reset", "Folder views were reset to Windows defaults.")

    def _start_view_task(self, kind: str, success_message: str) -> str:
        """Run a folder view registry task on a background thread.

        The work kills and relaunches Explorer, which takes seconds; it must
        not run on the GUI thread. Completion is reported via views_status.
        """
        if self._views_thread is not None and self._views_thread.is_alive():
            return json.dumps({"ok": False, "error": "A folder-view task is already running."})

        # Stop the autosize worker so it does not fight the Explorer restart.
        if self._main_window is not None:
            try:
                self._main_window._explorer_service.stop()
            except Exception:
                LOG.exception("Stopping the Explorer service before the folder-view task failed.")

        from fenestra.services import explorer_views

        def work() -> None:
            try:
                if kind == "apply":
                    result = explorer_views.apply_details_default()
                else:
                    result = explorer_views.reset_folder_views()
                if result.get("ok"):
                    message = success_message
                    if not result.get("data", {}).get("restarted", True):
                        message += " Restart File Explorer to see the change."
                    self.views_status.emit(message, 6000, STATUS_SUCCESS)
                else:
                    self.views_status.emit(
                        f"Folder-view update failed: {result.get('error', 'Unknown error.')}",
                        8000,
                        STATUS_ERROR,
                    )
            except Exception as e:
                LOG.exception("The folder-view task failed.")
                self.views_status.emit(
                    f"Folder-view update failed. {str(e).rstrip('.')}.", 8000, STATUS_ERROR
                )
            finally:
                # Queued back to the GUI thread; restarts the autosize worker
                # if the setting is enabled.
                self.explorer_service_restart.emit()

        # Not a daemon: shutdown joins it so registry work is never killed
        # mid-write (see MainWindow._stop_background_threads).
        self._views_thread = threading.Thread(target=work, name="FenestraViewTask")
        self._views_thread.start()
        return json.dumps({"ok": True, "data": {"started": True}})

    def wait_for_view_task(self, timeout: float = 10.0) -> None:
        """Wait up to ``timeout`` seconds for an in-flight folder-view task."""
        thread = self._views_thread
        if thread is not None and thread.is_alive():
            LOG.info("Waiting for the folder-view task to finish before shutdown.")
            thread.join(timeout)

    # Window-command slot.

    @Slot(str, result=str)
    def setWindowCommand(self, command: str) -> str:
        """Execute a window management command (minimize or close)."""
        if command not in ("minimize", "close"):
            return json.dumps({"ok": False, "error": f"Unknown window command: {command!r}."})
        if self._main_window is None:
            return json.dumps({"ok": False, "error": "The main window is not ready."})
        try:
            if command == "minimize":
                self._main_window.showMinimized()
            elif command == "close":
                self._main_window.close()
            return json.dumps({"ok": True})
        except Exception as e:
            LOG.exception("The %r window command failed.", command)
            return json.dumps({"ok": False, "error": str(e)})

    @Slot(int, int, int, result=str)
    def set_hit_test_regions(
        self,
        interactive_width: int,
        controls_width: int,
        title_bar_height: int,
    ) -> str:
        """Update measured frontend chrome regions used by native hit testing."""
        if self._main_window is None:
            return json.dumps({"ok": False, "error": "The main window is not ready."})
        try:
            self._main_window.set_hit_test_regions(
                interactive_width,
                controls_width,
                title_bar_height,
            )
            return json.dumps({"ok": True})
        except (TypeError, ValueError) as error:
            return json.dumps({"ok": False, "error": str(error)})

    # Internal helpers.

    def _apply_side_effects(self, applied: dict, *, hotkeys_already_applied: bool = False) -> None:
        """Apply business logic side effects after settings are committed."""
        if not self._main_window:
            return
        mw = self._main_window

        if "ex_auto_size" in applied:
            mw._update_explorer_autosize_thread()

        if (
            not hotkeys_already_applied
            and ("snap_key" in applied or "restore_key" in applied)
            and hasattr(mw, "_hotkey_listener")
        ):
            mw._hotkey_listener.update_keys(mw.settings.snap_key, mw.settings.restore_key)

        if "theme" in applied:
            mw._apply_theme_mode(applied["theme"])

        if "run_at_startup" in applied:
            enabled = bool(applied["run_at_startup"])
            try:
                from fenestra.app.window import sync_startup_task

                sync_startup_task(enabled)
            except Exception:
                LOG.exception("Updating the logon task failed.")
                self.snap_status.emit(
                    "The run-at-startup setting was saved, but its logon task could not be "
                    "updated. Fenestra will retry at the next launch.",
                    8000,
                    STATUS_ERROR,
                )
            mw.action_run_at_startup.setChecked(enabled)

        if "minimize_to_tray" in applied:
            mw.minimize_to_tray_on_exit = bool(applied["minimize_to_tray"])
            mw.action_minimize_on_exit.setChecked(mw.minimize_to_tray_on_exit)
