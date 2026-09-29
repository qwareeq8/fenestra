"""Application entry point: logging, admin elevation, single instance, QApp, MainWindow."""

import argparse
import atexit
import ctypes
import json
import logging
import os
import platform
import struct
import subprocess
import sys
import tempfile
import time
from logging.handlers import RotatingFileHandler

from fenestra.app.config import (
    APP_NAME,
    APP_VERSION,
    INSTANCE_ACTIVATE_MESSAGE,
    INSTANCE_WINDOW_PROPERTY,
    LOG_DIR,
    LOG_FILE,
    ORGANIZATION,
)
from fenestra.platform.startup import launch_command, main_module_name
from fenestra.platform.win32_abi import (
    DWORD,
    KERNEL32,
    SHELL32,
    USER32,
    WNDENUMPROC,
    handle_value,
)

# The singleton is per session, so another signed-in user can run their own
# instance. The installer's AppMutex names the same object.
INSTANCE_MUTEX_NAME = f"Local\\{APP_NAME}_Mutex"

_PE_MACHINE_ARCHITECTURES = {
    0x014C: "x86",
    0x8664: "x64",
    0xAA64: "arm64",
}
_SMOKE_REPORT_SCHEMA_VERSION = 1
_SMOKE_WEBENGINE_MARKER = "fenestra-smoke-ready"
_SMOKE_WEBENGINE_TITLE = "Fenestra WebEngine Smoke"
_SMOKE_WEBENGINE_TIMEOUT_MS = 30_000


def _init_logger() -> logging.Logger:
    """Initialize a rotating file logger in a per-user location."""
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    log_dir = os.path.join(base, LOG_DIR)
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, LOG_FILE)
    logger = logging.getLogger(APP_NAME)
    # INFO by default; --debug or FENESTRA_DEBUG=1 enables verbose logging. A
    # resident tray app at DEBUG writes to disk continuously, so it is opt-in.
    level = logging.DEBUG if _is_truthy(os.environ.get("FENESTRA_DEBUG")) else logging.INFO
    logger.setLevel(level)
    logger.propagate = False

    existing_handler = None
    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler) and getattr(
            handler, "baseFilename", None
        ) == os.path.abspath(log_path):
            existing_handler = handler
            break

    if existing_handler is None:
        handler = RotatingFileHandler(
            log_path,
            maxBytes=512 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        handler.setLevel(level)
        logger.addHandler(handler)
        existing_handler = handler

    # Add a console handler for immediate feedback during development.
    console_handler = None
    for h in logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler):
            console_handler = h
            break

    if console_handler is None and sys.stderr is not None:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        console_handler.setLevel(logging.INFO)  # The console shows INFO and higher levels.
        logger.addHandler(console_handler)

    logger.log_path = getattr(existing_handler, "baseFilename", log_path)
    return logger


def _is_admin() -> bool:
    try:
        return bool(SHELL32.IsUserAnAdmin())
    except Exception:
        return False


def _instance_already_running() -> bool:
    """Return True when another Fenestra instance holds the singleton mutex.

    Uses OpenMutexW so it works before elevation and never creates the mutex;
    the authoritative CreateMutex happens later in the elevated process.
    """
    SYNCHRONIZE = 0x00100000
    handle = KERNEL32.OpenMutexW(SYNCHRONIZE, False, INSTANCE_MUTEX_NAME)
    if handle:
        KERNEL32.CloseHandle(handle)
        return True
    # A filtered non-administrator token may be unable to open an elevated
    # instance's mutex. Access denied still proves that the named object exists.
    get_last_error = getattr(ctypes, "get_last_error", None)
    return bool(get_last_error is not None and get_last_error() == 5)


def _remove_startup_task() -> int:
    """Remove every user's Fenestra logon task for installer-driven uninstall.

    The uninstaller runs this entry elevated because deleting a
    highest-run-level task requires administrator rights.
    """
    try:
        from fenestra.platform.startup import remove_all_startup_tasks

        remove_all_startup_tasks()
        return 0
    except Exception:
        return 1


def _find_running_instance_window() -> int:
    """Return the marked main window of the running instance, or 0."""
    found = 0

    @WNDENUMPROC
    def find_marked_window(hwnd, _context):
        nonlocal found
        if USER32.GetPropW(hwnd, INSTANCE_WINDOW_PROPERTY):
            found = handle_value(hwnd)
            return False
        return True

    USER32.EnumWindows(find_marked_window, 0)
    return found


def _focus_running_instance() -> None:
    """Ask the running instance to show its window, on a best-effort basis.

    The running instance is elevated and this launch usually is not, so User
    Interface Privilege Isolation can block a direct ShowWindow, and a window
    shown behind Qt's back leaves Qt's visibility state wrong. This launch
    therefore posts the registered activation message, which the instance
    admits through its message filter and answers by restoring its own
    window. As the foreground process, this launch also lets the instance
    take the foreground.
    """
    try:
        hwnd = _find_running_instance_window()
        if not hwnd:
            return
        message = USER32.RegisterWindowMessageW(INSTANCE_ACTIVATE_MESSAGE)
        if not message:
            return
        process_id = DWORD()
        if USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id)) and process_id.value:
            USER32.AllowSetForegroundWindow(process_id.value)
        USER32.PostMessageW(hwnd, message, 0, 0)
    except Exception:
        pass


def _notify_already_running(logger: logging.Logger) -> None:
    """Focus and report an existing instance from either launch race path."""
    _focus_running_instance()
    logger.info("Fenestra is already running; focusing the existing window.")
    try:
        MB_ICONINFORMATION = 0x40
        USER32.MessageBoxW(
            None,
            "Fenestra is already running. Check the system tray.",
            APP_NAME,
            MB_ICONINFORMATION,
        )
    except Exception:
        pass


def _elevation_target_and_arguments(
    executable: str,
    argv: list[str],
    *,
    frozen: bool,
    main_module: str | None = None,
) -> tuple[str, str]:
    """Return an elevation target and Windows-quoted argument string."""
    target, arguments = launch_command(executable, argv, frozen=frozen, main_module=main_module)
    return target, subprocess.list2cmdline(arguments)


# UAC starts the elevated process with a fresh environment, so these
# development switches cross the relaunch as command-line flags.
_FORWARDED_SWITCHES = (("--dev", "FENESTRA_DEV"), ("--debug", "FENESTRA_DEBUG"))


def _is_truthy(value: str | None) -> bool:
    """Return whether an environment value enables a switch."""
    return (value or "").strip().lower() in ("1", "true", "yes")


def _with_forwarded_switches(argv: list[str], environ) -> list[str]:
    """Return ``argv`` plus a flag for each development switch set in ``environ``."""
    forwarded = list(argv)
    for flag, variable in _FORWARDED_SWITCHES:
        if _is_truthy(environ.get(variable)) and flag not in forwarded:
            forwarded.append(flag)
    return forwarded


def _apply_switch_flags(args: argparse.Namespace, environ) -> None:
    """Turn development flags back into the environment switches the app reads."""
    for flag, variable in _FORWARDED_SWITCHES:
        if getattr(args, flag.removeprefix("--")):
            environ[variable] = "1"


def _report_elevation_failure(logger: logging.Logger, result: object) -> None:
    """Report a failed or cancelled UAC elevation without assuming a console."""
    message = "Fenestra could not start because administrator elevation was cancelled or failed."
    if sys.stderr is not None:
        try:
            sys.stderr.write(f"{message}\n")
        except Exception:
            pass
    logger.error("Elevation failed. ShellExecuteW returned %s.", result)
    try:
        MB_ICONERROR = 0x10
        USER32.MessageBoxW(None, message, APP_NAME, MB_ICONERROR)
    except Exception:
        pass


def _normalize_architecture_name(value: str) -> str:
    """Normalize common process-architecture labels used by Python and Windows."""
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {"amd64", "x64", "x86-64"}:
        return "x64"
    if normalized in {"arm64", "aarch64"}:
        return "arm64"
    if normalized in {"x86", "i386", "i686"}:
        return "x86"
    return "unknown"


def _read_pe_machine(path: str) -> int | None:
    """Read a PE Machine value, or return None when the file is not a PE image."""
    with open(path, "rb") as executable:
        if executable.read(2) != b"MZ":
            return None
        executable.seek(0x3C)
        pe_offset_bytes = executable.read(4)
        if len(pe_offset_bytes) != 4:
            raise ValueError(f"PE header offset is truncated in {path}.")
        pe_offset = int.from_bytes(pe_offset_bytes, "little")
        executable.seek(pe_offset)
        if executable.read(4) != b"PE\0\0":
            raise ValueError(f"PE signature is missing in {path}.")
        machine_bytes = executable.read(2)
        if len(machine_bytes) != 2:
            raise ValueError(f"PE Machine field is truncated in {path}.")
        return int.from_bytes(machine_bytes, "little")


def _get_process_architecture(executable: str | None = None) -> str:
    """Return the active executable's load architecture without trusting the host OS."""
    executable_path = executable or sys.executable
    try:
        machine = _read_pe_machine(executable_path)
    except (OSError, ValueError):
        machine = None
    if machine is not None:
        return _PE_MACHINE_ARCHITECTURES.get(machine, "unknown")
    return _normalize_architecture_name(platform.machine())


def _new_smoke_report(checks: list[dict]) -> dict:
    """Create the architecture-bearing smoke evidence shared by source and frozen runs."""
    return {
        "schemaVersion": _SMOKE_REPORT_SCHEMA_VERSION,
        "application": APP_NAME,
        "version": APP_VERSION,
        "executable": sys.executable,
        "frozen": bool(getattr(sys, "frozen", False)),
        "platformMachine": platform.machine(),
        "processArchitecture": _get_process_architecture(),
        "pointerBits": struct.calcsize("P") * 8,
        "checks": checks,
    }


def _wait_for_qt_callback(QtCore, start, description: str):
    """Wait for one asynchronous Qt callback and fail with a bounded diagnostic."""
    event_loop = QtCore.QEventLoop()
    timeout = QtCore.QTimer()
    timeout.setSingleShot(True)
    outcome: dict[str, tuple] = {}

    def complete(*args):
        if "args" not in outcome:
            outcome["args"] = args
        event_loop.quit()

    timeout.timeout.connect(event_loop.quit)
    timeout.start(_SMOKE_WEBENGINE_TIMEOUT_MS)
    try:
        start(complete)
        if "args" not in outcome:
            event_loop.exec()
    finally:
        timeout.stop()

    if "args" not in outcome:
        raise TimeoutError(f"Timed out after {_SMOKE_WEBENGINE_TIMEOUT_MS} ms while {description}.")
    return outcome["args"]


def _exercise_webengine_page(app) -> dict:
    """Load an in-memory page through Fenestra's real WebEngine and WebChannel stack."""
    import comtypes
    from PySide6 import QtCore
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineWidgets import QWebEngineView

    from fenestra.app import webview as webview_module
    from fenestra.services import explorer_columns

    view = QWebEngineView()
    page = webview_module.FenestraWebPage(view)
    page.set_navigation_policy(None, False)
    channel = QWebChannel(page)
    page.setWebChannel(channel)
    view.setPage(page)
    render_terminations: list[dict] = []

    def record_termination(status, exit_code):
        render_terminations.append({"status": str(status), "exitCode": int(exit_code)})

    page.renderProcessTerminated.connect(record_termination)
    html = (
        "<!doctype html><html><head>"
        f"<title>{_SMOKE_WEBENGINE_TITLE}</title>"
        "</head><body>"
        f'<main id="{_SMOKE_WEBENGINE_MARKER}">{_SMOKE_WEBENGINE_MARKER}</main>'
        "</body></html>"
    )

    try:

        def begin_load(callback):
            page.loadFinished.connect(callback)
            page.setHtml(html, QtCore.QUrl("about:blank"))

        load_result = _wait_for_qt_callback(QtCore, begin_load, "loading the WebEngine smoke page")
        loaded = bool(load_result[0]) if load_result else False
        if not loaded:
            raise RuntimeError(
                "Qt WebEngine loadFinished reported failure. "
                f"Render-process terminations: {render_terminations or 'none'}."
            )

        html_result = _wait_for_qt_callback(
            QtCore,
            page.toHtml,
            "reading the loaded WebEngine smoke document",
        )
        loaded_html = str(html_result[0]) if html_result else ""
        title = page.title()
        if title != _SMOKE_WEBENGINE_TITLE:
            raise RuntimeError(
                f"Qt WebEngine loaded an unexpected title: {title!r}; "
                f"expected {_SMOKE_WEBENGINE_TITLE!r}."
            )
        if _SMOKE_WEBENGINE_MARKER not in loaded_html:
            raise RuntimeError(
                f"Qt WebEngine document is missing marker {_SMOKE_WEBENGINE_MARKER!r}."
            )
        if render_terminations:
            raise RuntimeError(
                "Qt WebEngine render process terminated during the smoke load: "
                f"{render_terminations}."
            )
        return {
            "loadFinished": True,
            "title": title,
            "documentMarker": _SMOKE_WEBENGINE_MARKER,
            "documentBytes": len(loaded_html.encode("utf-8")),
            "webEngineView": type(view).__name__,
            "webEnginePage": type(page).__name__,
            "webChannel": type(channel).__name__,
            "webviewModule": os.path.abspath(webview_module.__file__),
            "explorerColumnsModule": os.path.abspath(explorer_columns.__file__),
            "comtypesModule": os.path.abspath(comtypes.__file__),
        }
    finally:
        view.deleteLater()
        app.processEvents()


def _write_smoke_report(path: str, report: dict) -> None:
    """Write the smoke result atomically for windowed builds and CI."""
    absolute_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
    temporary_path = f"{absolute_path}.{os.getpid()}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary_path, absolute_path)


def _create_smoke_settings_backend(QtCore, directory: str):
    """Create an explicit temporary INI backend for one smoke-test settings object."""
    settings_path = os.path.join(directory, "settings.ini")
    return QtCore.QSettings(settings_path, QtCore.QSettings.Format.IniFormat)


def _run_smoke_test(report_path: str | None = None) -> int:
    """Run non-interactive boot verification without touching user settings."""
    with tempfile.TemporaryDirectory(prefix="fenestra-smoke-settings-") as settings_directory:
        return _run_isolated_smoke_test(report_path, settings_directory)


def _run_isolated_smoke_test(report_path: str | None, settings_directory: str) -> int:
    """Run smoke checks against an isolated settings directory."""
    checks: list[dict] = []
    report = _new_smoke_report(checks)

    try:
        from PySide6 import QtCore, QtWidgets

        QtCore.QCoreApplication.setOrganizationName(ORGANIZATION)
        QtCore.QCoreApplication.setApplicationName(APP_NAME)
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        report["qtVersion"] = QtCore.qVersion()
    except Exception as error:
        checks.append(
            {
                "name": "Qt application initialization",
                "passed": False,
                "error": f"{type(error).__name__}: {error}",
            }
        )
        report["passed"] = 0
        report["failed"] = 1
        report["exitCode"] = 1
        if report_path:
            _write_smoke_report(report_path, report)
        return 1

    def check(name, fn):
        started = time.perf_counter()
        try:
            details = fn()
            print(f"  PASS  {name}")
            result = {
                "name": name,
                "passed": True,
                "durationMs": round((time.perf_counter() - started) * 1000, 3),
            }
            if isinstance(details, dict):
                result["details"] = details
            checks.append(result)
        except Exception as error:
            print(f"  FAIL  {name}: {error}")
            checks.append(
                {
                    "name": name,
                    "passed": False,
                    "durationMs": round((time.perf_counter() - started) * 1000, 3),
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    print("Running the Fenestra smoke test.")
    print("=" * 40)

    # Check 1: process metadata is a supported 64-bit application architecture.
    def _check_process_metadata():
        architecture = report["processArchitecture"]
        pointer_bits = report["pointerBits"]
        if architecture not in {"x64", "arm64"}:
            raise RuntimeError(f"Unsupported process architecture: {architecture!r}.")
        if pointer_bits != 64:
            raise RuntimeError(f"Expected a 64-bit process; found {pointer_bits} bits.")
        return {"processArchitecture": architecture, "pointerBits": pointer_bits}

    check("Process architecture metadata", _check_process_metadata)

    # Check 2: icon.ico resource path resolves and file exists.
    def _check_icon():
        from fenestra.platform.resources import resource_path

        path = resource_path("icon.ico")
        if not os.path.exists(path):
            raise FileNotFoundError(f"The icon.ico resource was not found at {path}.")

    check("icon.ico resource path", _check_icon)

    # Check 3: frontend/dist/ exists and contains index.html.
    def _check_frontend():
        from fenestra.platform.resources import resource_path

        index_path = os.path.join(resource_path("frontend"), "dist", "index.html")
        if not os.path.exists(index_path):
            raise FileNotFoundError(
                f"The built frontend/dist/index.html was not found at {index_path}."
            )

    check("frontend/dist/index.html exists", _check_frontend)

    # Check 4: Qt WebEngine launches its helper and loads an in-memory document.
    def _check_webengine():
        return _exercise_webengine_page(app)

    check("Qt WebEngine minimal page load", _check_webengine)

    # Check 5: Settings reads/writes without exceptions.
    def _new_isolated_settings():
        from fenestra.settings.persistence import Settings

        backend = _create_smoke_settings_backend(QtCore, settings_directory)
        return Settings(backend)

    def _check_settings():
        s = _new_isolated_settings()
        _ = s.snap_key  # Read a known key.
        s.save()
        backend_path = os.path.abspath(s._qs.fileName())
        settings_root = os.path.abspath(settings_directory)
        if os.path.commonpath((backend_path, settings_root)) != settings_root:
            raise RuntimeError(f"Smoke settings escaped the temporary directory: {backend_path}.")
        if s._qs.format() != QtCore.QSettings.Format.IniFormat:
            raise RuntimeError(f"Smoke settings used an unexpected format: {s._qs.format()}.")
        return {"backend": backend_path, "format": "IniFormat"}

    check("Settings read/write", _check_settings)

    # Check 6: SettingsState initializes with valid defaults.
    def _check_settings_state():
        from fenestra.settings.state import SettingsState

        s = _new_isolated_settings()
        state = SettingsState(s)
        json_str = state.get_json()
        assert len(json_str) > 2, "SettingsState.get_json() returned an empty payload."

    check("SettingsState defaults", _check_settings_state)

    # Check 7: FenestraBridge initializes without errors.
    def _check_bridge():
        from fenestra.bridge.bridge import FenestraBridge
        from fenestra.services.snap import SnapService
        from fenestra.settings.state import SettingsState

        s = _new_isolated_settings()
        state = SettingsState(s)
        snap_svc = SnapService(None)
        bridge = FenestraBridge(state, snap_svc)
        assert bridge is not None

    check("FenestraBridge initialization", _check_bridge)

    passed = sum(1 for item in checks if item["passed"])
    failed = len(checks) - passed
    exit_code = 0 if failed == 0 else 1
    report.update({"passed": passed, "failed": failed, "exitCode": exit_code})

    print(f"\n{passed} passed, {failed} failed")
    if report_path:
        try:
            _write_smoke_report(report_path, report)
        except Exception as error:
            print(f"  FAIL  The smoke report could not be written. {error}")
            return 1
    return exit_code


def main() -> None:
    """Elevate the process, initialize logging, and launch ``MainWindow``."""
    # Exit early on non-Windows platforms.
    if sys.platform != "win32":
        print("Fenestra requires Windows.")
        sys.exit(1)

    # Parse ``--smoke-test`` before elevation to avoid a UAC loop.
    parser = argparse.ArgumentParser(prog="fenestra", add_help=False)
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Run non-interactive boot verification and exit.",
    )
    parser.add_argument(
        "--smoke-report",
        metavar="PATH",
        help="Write the smoke-test result as machine-readable JSON.",
    )
    parser.add_argument(
        "--remove-startup-task",
        action="store_true",
        help="Remove Fenestra's elevated logon task and exit.",
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="Load the frontend from the Vite development server.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Write verbose debug logs.",
    )
    args, _ = parser.parse_known_args()
    _apply_switch_flags(args, os.environ)

    if args.smoke_test:
        sys.exit(_run_smoke_test(args.smoke_report))
    if args.remove_startup_task:
        sys.exit(_remove_startup_task())

    import faulthandler

    LOG = _init_logger()

    _CRASH_LOG = None
    try:
        crash_log_path = os.path.join(os.path.dirname(getattr(LOG, "log_path", "")), "crash.log")
        _CRASH_LOG = open(crash_log_path, "a", encoding="utf-8")
        faulthandler.enable(_CRASH_LOG)
    except Exception:
        try:
            faulthandler.enable()
        except Exception:
            pass

    def _cleanup_faulthandler() -> None:
        """Close the optional crash-log handle during process teardown."""
        if _CRASH_LOG:
            try:
                _CRASH_LOG.close()
            except Exception:
                pass

    atexit.register(_cleanup_faulthandler)

    # Detect an already-running instance before elevating so a second launch
    # does not show a pointless UAC prompt and then exit silently.
    if _instance_already_running():
        _notify_already_running(LOG)
        return

    if not _is_admin():
        exe, arguments = _elevation_target_and_arguments(
            sys.executable,
            _with_forwarded_switches(sys.argv, os.environ),
            frozen=bool(getattr(sys, "frozen", False)),
            main_module=main_module_name(),
        )
        hinst = SHELL32.ShellExecuteW(None, "runas", exe, arguments, None, 1)
        if handle_value(hinst) <= 32:
            _report_elevation_failure(LOG, hinst)
            return
        return  # The elevated child will run the app.

    import win32api
    import win32event
    import winerror
    from PySide6 import QtCore, QtWidgets

    from fenestra.app.window import MainWindow

    # Qt 6 establishes Per-Monitor V2 awareness when QApplication initializes.
    # Calling the legacy SHCore API first would lock the process to Per-Monitor V1.
    try:
        SHELL32.SetCurrentProcessExplicitAppUserModelID("com.yusufqwareeq.fenestra")
    except Exception:
        pass

    QtCore.QCoreApplication.setOrganizationName(ORGANIZATION)
    QtCore.QCoreApplication.setApplicationName(APP_NAME)

    mutex = win32event.CreateMutex(None, False, INSTANCE_MUTEX_NAME)
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        _notify_already_running(LOG)
        return

    app = QtWidgets.QApplication(sys.argv)
    # Set this only after ``QApplication`` exists.
    QtWidgets.QApplication.setQuitOnLastWindowClosed(False)

    if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        QtWidgets.QMessageBox.critical(None, "Error", "No system tray is available. Exiting.")
        return
    win = MainWindow()

    shutdown_started = False

    def _shutdown():
        """Idempotent teardown: stop workers and unhook the global hotkeys."""
        nonlocal shutdown_started
        if shutdown_started:
            return
        shutdown_started = True
        try:
            win._stop_background_threads()
        except Exception:
            LOG.exception("Background thread teardown failed.")
        try:
            win._hotkey_listener.cleanup()
        except Exception:
            LOG.exception("Hotkey listener cleanup failed.")

    app.aboutToQuit.connect(_shutdown)
    atexit.register(_shutdown)

    win._singleton_mutex = mutex
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
