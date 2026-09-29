"""Tests for pointer-safe Win32 ABI declarations and call paths."""

from __future__ import annotations

import ctypes
import importlib
import logging
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

try:
    import comtypes as _comtypes  # type: ignore[import-untyped]
except ImportError:
    _HAS_COMTYPES_ABI = False
else:
    _HAS_COMTYPES_ABI = hasattr(_comtypes, "GUID") and hasattr(_comtypes, "POINTER")

from fenestra.platform.win32_abi import (
    ADVAPI32,
    DWMAPI,
    DWORD,
    HANDLE,
    HINSTANCE,
    HWND,
    KERNEL32,
    LPARAM,
    LRESULT,
    PROCESS_INFORMATION,
    SECURITY_ATTRIBUTES,
    SHELL32,
    STARTUPINFOW,
    USER32,
    WPARAM,
    handle_value,
)

HIGH_HWND = 0x1234_5678_ABCD_EF01
HIGH_PROCESS = 0x2234_5678_ABCD_EF02
HIGH_TOKEN = 0x3234_5678_ABCD_EF03
HIGH_PRIMARY_TOKEN = 0x4234_5678_ABCD_EF04


@pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason="A 64-bit process is required.")
def test_pointer_sized_types_preserve_high_handle_bits() -> None:
    """Pointer-sized Win32 aliases preserve values above 32 bits."""

    assert ctypes.sizeof(HANDLE) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(HWND) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(HINSTANCE) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(WPARAM) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(LPARAM) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(LRESULT) == ctypes.sizeof(ctypes.c_void_p)
    assert handle_value(HANDLE(HIGH_HWND)) == HIGH_HWND


@pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason="A 64-bit process is required.")
def test_pointer_sensitive_structures_have_64_bit_windows_layouts() -> None:
    """Process-launch structures retain their documented 64-bit layouts."""

    assert ctypes.sizeof(SECURITY_ATTRIBUTES) == 24
    assert ctypes.sizeof(STARTUPINFOW) == 104
    assert ctypes.sizeof(PROCESS_INFORMATION) == 24


def test_singleton_mutex_path_preserves_high_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    """The singleton check closes the same pointer-sized handle it opened."""

    app_main = importlib.import_module("fenestra.app.__main__")
    kernel32 = SimpleNamespace(
        OpenMutexW=MagicMock(return_value=HIGH_PROCESS),
        CloseHandle=MagicMock(return_value=True),
    )
    monkeypatch.setattr(app_main, "KERNEL32", kernel32)

    assert app_main._instance_already_running() is True
    kernel32.CloseHandle.assert_called_once_with(HIGH_PROCESS)


@pytest.mark.parametrize(("last_error", "expected"), [(2, False), (5, True)])
def test_singleton_mutex_distinguishes_missing_from_access_denied(
    monkeypatch: pytest.MonkeyPatch,
    last_error: int,
    expected: bool,
) -> None:
    """Access denied proves the mutex exists, while a missing object does not."""
    app_main = importlib.import_module("fenestra.app.__main__")
    kernel32 = SimpleNamespace(
        OpenMutexW=MagicMock(return_value=0),
        CloseHandle=MagicMock(return_value=True),
    )
    monkeypatch.setattr(app_main, "KERNEL32", kernel32)
    monkeypatch.setattr(app_main.ctypes, "get_last_error", lambda: last_error, raising=False)

    assert app_main._instance_already_running() is expected
    kernel32.CloseHandle.assert_not_called()


ACTIVATE_MESSAGE = 0xC123
RUNNING_PROCESS_ID = 4242


def _focus_user32(enumerate_windows, marked: bool) -> SimpleNamespace:
    """Return a USER32 double for the second-launch activation path."""

    def get_window_process(_hwnd, process_id_pointer):
        process_id_pointer._obj.value = RUNNING_PROCESS_ID
        return 1

    return SimpleNamespace(
        EnumWindows=MagicMock(side_effect=enumerate_windows),
        GetPropW=MagicMock(return_value=1 if marked else 0),
        RegisterWindowMessageW=MagicMock(return_value=ACTIVATE_MESSAGE),
        GetWindowThreadProcessId=MagicMock(side_effect=get_window_process),
        AllowSetForegroundWindow=MagicMock(return_value=True),
        PostMessageW=MagicMock(return_value=True),
        ShowWindow=MagicMock(return_value=True),
        SetForegroundWindow=MagicMock(return_value=True),
    )


def test_focus_path_signals_the_instance_instead_of_showing_its_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second launch posts the activation message to the marked high HWND.

    It never shows the elevated window itself, which UIPI can block and which
    would leave Qt's visibility state out of sync.
    """

    app_main = importlib.import_module("fenestra.app.__main__")

    def enumerate_windows(callback, context):
        assert not callback(HIGH_HWND, context)
        return True

    user32 = _focus_user32(enumerate_windows, marked=True)
    monkeypatch.setattr(app_main, "USER32", user32)

    app_main._focus_running_instance()

    user32.RegisterWindowMessageW.assert_called_once_with("Fenestra.ActivateInstance.v1")
    user32.AllowSetForegroundWindow.assert_called_once_with(RUNNING_PROCESS_ID)
    user32.PostMessageW.assert_called_once_with(HIGH_HWND, ACTIVATE_MESSAGE, 0, 0)
    user32.ShowWindow.assert_not_called()
    user32.SetForegroundWindow.assert_not_called()


def test_focus_path_ignores_an_unmarked_same_title_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrelated window cannot be signaled merely because its title is Fenestra."""

    app_main = importlib.import_module("fenestra.app.__main__")

    def enumerate_windows(callback, context):
        assert callback(HIGH_HWND, context)
        return True

    user32 = _focus_user32(enumerate_windows, marked=False)
    monkeypatch.setattr(app_main, "USER32", user32)

    app_main._focus_running_instance()

    user32.PostMessageW.assert_not_called()
    user32.AllowSetForegroundWindow.assert_not_called()


def test_existing_instance_notification_focuses_and_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Either singleton race path provides the same focus and message feedback."""
    app_main = importlib.import_module("fenestra.app.__main__")
    focus = MagicMock()
    message_box = MagicMock(return_value=1)
    logger = MagicMock()
    monkeypatch.setattr(app_main, "_focus_running_instance", focus)
    monkeypatch.setattr(app_main, "USER32", SimpleNamespace(MessageBoxW=message_box))

    app_main._notify_already_running(logger)

    focus.assert_called_once_with()
    logger.info.assert_called_once()
    message_box.assert_called_once()


def test_elevation_arguments_use_windows_command_line_quoting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source relaunch preserves spaces, embedded quotes, and trailing slashes."""
    app_main = importlib.import_module("fenestra.app.__main__")
    argv = [
        r"C:\Program Files\Fenestra\main.py",
        "value with spaces",
        'embedded"quote',
        "trailing\\",
    ]
    startup = importlib.import_module("fenestra.platform.startup")
    monkeypatch.setattr(startup, "select_pythonw_executable", lambda _value, _exists: "pythonw.exe")

    target, arguments = app_main._elevation_target_and_arguments(
        "python.exe",
        argv,
        frozen=False,
    )

    assert target == "pythonw.exe"
    assert arguments == subprocess.list2cmdline([app_main.os.path.abspath(argv[0]), *argv[1:]])


def test_elevation_restarts_a_module_entry_point_with_dash_m(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``python -m`` launch is not replayed as a script inside the package."""
    app_main = importlib.import_module("fenestra.app.__main__")
    startup = importlib.import_module("fenestra.platform.startup")
    monkeypatch.setattr(startup, "select_pythonw_executable", lambda _value, _exists: "pythonw.exe")

    target, arguments = app_main._elevation_target_and_arguments(
        "python.exe",
        [r"C:\src\fenestra\__main__.py", "--flag"],
        frozen=False,
        main_module="fenestra",
    )

    assert (target, arguments) == ("pythonw.exe", "-m fenestra --flag")


def test_development_switches_survive_the_uac_relaunch() -> None:
    """Environment switches become flags, and flags become switches again."""
    app_main = importlib.import_module("fenestra.app.__main__")

    forwarded = app_main._with_forwarded_switches(
        ["main.py", "--debug"], {"FENESTRA_DEV": "1", "FENESTRA_DEBUG": "true"}
    )
    restored: dict[str, str] = {}
    app_main._apply_switch_flags(SimpleNamespace(dev=True, debug=False), restored)

    assert forwarded == ["main.py", "--debug", "--dev"]
    assert restored == {"FENESTRA_DEV": "1"}


def test_elevation_failure_does_not_require_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A windowed frozen process reports UAC failure without writing to None."""
    app_main = importlib.import_module("fenestra.app.__main__")
    logger = MagicMock()
    message_box = MagicMock(return_value=1)
    monkeypatch.setattr(app_main.sys, "stderr", None)
    monkeypatch.setattr(app_main, "USER32", SimpleNamespace(MessageBoxW=message_box))

    app_main._report_elevation_failure(logger, 5)

    logger.error.assert_called_once()
    message_box.assert_called_once()


def test_windowed_logger_omits_console_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A console-free build logs to disk without installing a None stream."""
    app_main = importlib.import_module("fenestra.app.__main__")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(app_main, "APP_NAME", "FenestraLoggerTest")
    monkeypatch.setattr(app_main, "LOG_DIR", "logs")
    monkeypatch.setattr(app_main, "LOG_FILE", "test.log")
    monkeypatch.setattr(app_main.sys, "stderr", None)

    logger = app_main._init_logger()
    try:
        console_handlers = [
            handler
            for handler in logger.handlers
            if isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, logging.handlers.RotatingFileHandler)
        ]
        assert console_handlers == []
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def _store_dword(pointer: Any, value: int) -> None:
    ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32)).contents.value = value


def _store_handle(pointer: Any, value: int) -> None:
    ctypes.cast(pointer, ctypes.POINTER(HANDLE)).contents.value = value


def test_shell_token_path_preserves_all_high_handles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shell token duplication never narrows HWND, process, or token handles."""

    from fenestra.services import explorer_views

    def get_shell_pid(_hwnd: int, pid: Any) -> int:
        _store_dword(pid, 4321)
        return 1

    def open_process_token(_process: int, _access: int, token: Any) -> int:
        _store_handle(token, HIGH_TOKEN)
        return 1

    def duplicate_token(
        _token: Any,
        _access: int,
        _attributes: Any,
        _level: int,
        _kind: int,
        primary: Any,
    ) -> int:
        _store_handle(primary, HIGH_PRIMARY_TOKEN)
        return 1

    user32 = SimpleNamespace(
        GetShellWindow=MagicMock(return_value=HIGH_HWND),
        GetWindowThreadProcessId=MagicMock(side_effect=get_shell_pid),
    )
    kernel32 = SimpleNamespace(
        OpenProcess=MagicMock(return_value=HIGH_PROCESS),
        CloseHandle=MagicMock(return_value=True),
    )
    advapi32 = SimpleNamespace(
        OpenProcessToken=MagicMock(side_effect=open_process_token),
        DuplicateTokenEx=MagicMock(side_effect=duplicate_token),
    )
    monkeypatch.setattr(explorer_views, "USER32", user32)
    monkeypatch.setattr(explorer_views, "KERNEL32", kernel32)
    monkeypatch.setattr(explorer_views, "ADVAPI32", advapi32)

    primary = explorer_views._shell_token()

    assert handle_value(primary) == HIGH_PRIMARY_TOKEN
    assert user32.GetWindowThreadProcessId.call_args.args[0] == HIGH_HWND
    assert advapi32.OpenProcessToken.call_args.args[0] == HIGH_PROCESS
    assert handle_value(advapi32.DuplicateTokenEx.call_args.args[0]) == HIGH_TOKEN
    closed = [handle_value(call.args[0]) for call in kernel32.CloseHandle.call_args_list]
    assert closed == [HIGH_TOKEN, HIGH_PROCESS]


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 DLL prototypes require Windows.")
def test_loaded_dll_functions_have_pointer_safe_prototypes() -> None:
    """The real DLL function objects expose exact handle types."""

    assert KERNEL32.OpenMutexW.restype is HANDLE
    assert KERNEL32.CloseHandle.argtypes == (HANDLE,)
    assert KERNEL32.OpenProcess.restype is HANDLE
    assert KERNEL32.QueryFullProcessImageNameW.argtypes == (
        HANDLE,
        DWORD,
        ctypes.c_wchar_p,
        ctypes.POINTER(DWORD),
    )
    assert USER32.GetForegroundWindow.restype is HWND
    assert USER32.RegisterWindowMessageW.restype is ctypes.c_uint32
    assert USER32.PostMessageW.argtypes == (HWND, ctypes.c_uint32, WPARAM, LPARAM)
    assert USER32.ChangeWindowMessageFilterEx.argtypes[0] is HWND
    assert USER32.AllowSetForegroundWindow.argtypes == (DWORD,)
    assert USER32.IsHungAppWindow.argtypes == (HWND,)
    assert USER32.GetShellWindow.restype is HWND
    assert USER32.GetWindowThreadProcessId.argtypes[0] is HWND
    assert USER32.SetPropW.argtypes == (HWND, ctypes.c_wchar_p, HANDLE)
    assert USER32.GetPropW.restype is HANDLE
    assert USER32.RemovePropW.restype is HANDLE
    assert SHELL32.ShellExecuteW.restype is HINSTANCE
    assert DWMAPI.DwmGetWindowAttribute.argtypes[0] is HWND
    assert ADVAPI32.OpenProcessToken.argtypes[0] is HANDLE
    assert ADVAPI32.DuplicateTokenEx.argtypes[0] is HANDLE
    assert ADVAPI32.CreateProcessWithTokenW.argtypes[0] is HANDLE


@pytest.mark.skipif(
    sys.platform != "win32" or not _HAS_COMTYPES_ABI,
    reason="The real Windows comtypes ABI is required.",
)
def test_query_service_uses_guid_pointers() -> None:
    """IServiceProvider passes both GUID inputs by pointer on every ABI."""

    from comtypes import GUID, POINTER  # type: ignore[import-untyped]

    from fenestra.services.explorer_columns import IServiceProvider

    query_service = IServiceProvider._methods_[0]
    assert query_service.argtypes[0] == POINTER(GUID)
    assert query_service.argtypes[1] == POINTER(GUID)
