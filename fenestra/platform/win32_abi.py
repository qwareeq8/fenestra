"""Pointer-safe Win32 type declarations and function prototypes.

The application supports 64-bit Windows processes, where handles and message
parameters are pointer-sized.  Keeping every direct ``ctypes`` declaration in
one module prevents the default ``c_int`` conversion from truncating those
values on x64 or ARM64.

The scalar aliases and structures remain importable on non-Windows systems so
pure ABI tests can run in Linux CI.  The DLL objects are loaded only on Windows.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Any

# Windows uses LLP64 on both x64 and ARM64.  Fixed-width scalar declarations
# keep the structure layouts testable even when this module is imported on an
# LP64 host such as Linux.
BOOL = ctypes.c_int32
BYTE = ctypes.c_uint8
DWORD = ctypes.c_uint32
INT = ctypes.c_int32
UINT = ctypes.c_uint32
WORD = ctypes.c_uint16
HRESULT = ctypes.c_int32

HANDLE = ctypes.c_void_p
HWND = ctypes.c_void_p
HINSTANCE = ctypes.c_void_p
LPVOID = ctypes.c_void_p
LPCWSTR = ctypes.c_wchar_p
LPWSTR = ctypes.c_wchar_p

ULONG_PTR = ctypes.c_size_t
LONG_PTR = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t
WNDENUMPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(BOOL, HWND, LPARAM)

LPDWORD = ctypes.POINTER(DWORD)


class SECURITY_ATTRIBUTES(ctypes.Structure):
    """Match the pointer-width-sensitive Win32 ``SECURITY_ATTRIBUTES`` layout."""

    _fields_ = [
        ("nLength", DWORD),
        ("lpSecurityDescriptor", LPVOID),
        ("bInheritHandle", BOOL),
    ]


class STARTUPINFOW(ctypes.Structure):
    """Match the Unicode Win32 ``STARTUPINFO`` layout."""

    _fields_ = [
        ("cb", DWORD),
        ("lpReserved", LPWSTR),
        ("lpDesktop", LPWSTR),
        ("lpTitle", LPWSTR),
        ("dwX", DWORD),
        ("dwY", DWORD),
        ("dwXSize", DWORD),
        ("dwYSize", DWORD),
        ("dwXCountChars", DWORD),
        ("dwYCountChars", DWORD),
        ("dwFillAttribute", DWORD),
        ("dwFlags", DWORD),
        ("wShowWindow", WORD),
        ("cbReserved2", WORD),
        ("lpReserved2", ctypes.POINTER(BYTE)),
        ("hStdInput", HANDLE),
        ("hStdOutput", HANDLE),
        ("hStdError", HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    """Match the pointer-width-sensitive Win32 ``PROCESS_INFORMATION`` layout."""

    _fields_ = [
        ("hProcess", HANDLE),
        ("hThread", HANDLE),
        ("dwProcessId", DWORD),
        ("dwThreadId", DWORD),
    ]


LPSECURITY_ATTRIBUTES = ctypes.POINTER(SECURITY_ATTRIBUTES)
LPSTARTUPINFOW = ctypes.POINTER(STARTUPINFOW)
LPPROCESS_INFORMATION = ctypes.POINTER(PROCESS_INFORMATION)


def handle_value(value: int | ctypes.c_void_p | None) -> int:
    """Return an opaque handle as an unsigned pointer-sized Python integer."""
    if value is None:
        return 0
    raw = getattr(value, "value", value)
    if raw is None:
        return 0
    return int(ctypes.c_size_t(int(raw)).value)


# ``Any`` prevents platform-conditional ``None`` values from leaking into every
# caller's type analysis.  Runtime callers are already guarded by the
# application's Windows-only entry point.
USER32: Any = None
KERNEL32: Any = None
SHELL32: Any = None
DWMAPI: Any = None
ADVAPI32: Any = None

if sys.platform == "win32":
    USER32 = ctypes.WinDLL("user32", use_last_error=True)
    KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    SHELL32 = ctypes.WinDLL("shell32", use_last_error=True)
    DWMAPI = ctypes.WinDLL("dwmapi", use_last_error=True)
    ADVAPI32 = ctypes.WinDLL("advapi32", use_last_error=True)

    USER32.GetForegroundWindow.argtypes = ()
    USER32.GetForegroundWindow.restype = HWND
    USER32.GetWindowRect.argtypes = (HWND, ctypes.POINTER(wintypes.RECT))
    USER32.GetWindowRect.restype = BOOL
    USER32.MoveWindow.argtypes = (HWND, INT, INT, INT, INT, BOOL)
    USER32.MoveWindow.restype = BOOL
    USER32.IsHungAppWindow.argtypes = (HWND,)
    USER32.IsHungAppWindow.restype = BOOL
    USER32.EnumWindows.argtypes = (WNDENUMPROC, LPARAM)
    USER32.EnumWindows.restype = BOOL
    USER32.RegisterWindowMessageW.argtypes = (LPCWSTR,)
    USER32.RegisterWindowMessageW.restype = UINT
    USER32.PostMessageW.argtypes = (HWND, UINT, WPARAM, LPARAM)
    USER32.PostMessageW.restype = BOOL
    USER32.ChangeWindowMessageFilterEx.argtypes = (HWND, UINT, DWORD, LPVOID)
    USER32.ChangeWindowMessageFilterEx.restype = BOOL
    USER32.AllowSetForegroundWindow.argtypes = (DWORD,)
    USER32.AllowSetForegroundWindow.restype = BOOL
    USER32.MessageBoxW.argtypes = (HWND, LPCWSTR, LPCWSTR, UINT)
    USER32.MessageBoxW.restype = INT
    USER32.GetShellWindow.argtypes = ()
    USER32.GetShellWindow.restype = HWND
    USER32.GetWindowThreadProcessId.argtypes = (HWND, LPDWORD)
    USER32.GetWindowThreadProcessId.restype = DWORD
    USER32.SetPropW.argtypes = (HWND, LPCWSTR, HANDLE)
    USER32.SetPropW.restype = BOOL
    USER32.GetPropW.argtypes = (HWND, LPCWSTR)
    USER32.GetPropW.restype = HANDLE
    USER32.RemovePropW.argtypes = (HWND, LPCWSTR)
    USER32.RemovePropW.restype = HANDLE
    KERNEL32.OpenMutexW.argtypes = (DWORD, BOOL, LPCWSTR)
    KERNEL32.OpenMutexW.restype = HANDLE
    KERNEL32.CloseHandle.argtypes = (HANDLE,)
    KERNEL32.CloseHandle.restype = BOOL
    KERNEL32.OpenProcess.argtypes = (DWORD, BOOL, DWORD)
    KERNEL32.OpenProcess.restype = HANDLE
    KERNEL32.QueryFullProcessImageNameW.argtypes = (HANDLE, DWORD, LPWSTR, LPDWORD)
    KERNEL32.QueryFullProcessImageNameW.restype = BOOL
    KERNEL32.ProcessIdToSessionId.argtypes = (DWORD, LPDWORD)
    KERNEL32.ProcessIdToSessionId.restype = BOOL

    SHELL32.IsUserAnAdmin.argtypes = ()
    SHELL32.IsUserAnAdmin.restype = BOOL
    SHELL32.ShellExecuteW.argtypes = (HWND, LPCWSTR, LPCWSTR, LPCWSTR, LPCWSTR, INT)
    SHELL32.ShellExecuteW.restype = HINSTANCE
    SHELL32.SetCurrentProcessExplicitAppUserModelID.argtypes = (LPCWSTR,)
    SHELL32.SetCurrentProcessExplicitAppUserModelID.restype = HRESULT

    DWMAPI.DwmGetWindowAttribute.argtypes = (HWND, DWORD, LPVOID, DWORD)
    DWMAPI.DwmGetWindowAttribute.restype = HRESULT

    ADVAPI32.OpenProcessToken.argtypes = (HANDLE, DWORD, ctypes.POINTER(HANDLE))
    ADVAPI32.OpenProcessToken.restype = BOOL
    ADVAPI32.DuplicateTokenEx.argtypes = (
        HANDLE,
        DWORD,
        LPSECURITY_ATTRIBUTES,
        INT,
        INT,
        ctypes.POINTER(HANDLE),
    )
    ADVAPI32.DuplicateTokenEx.restype = BOOL
    ADVAPI32.CreateProcessWithTokenW.argtypes = (
        HANDLE,
        DWORD,
        LPCWSTR,
        LPWSTR,
        DWORD,
        LPVOID,
        LPCWSTR,
        LPSTARTUPINFOW,
        LPPROCESS_INFORMATION,
    )
    ADVAPI32.CreateProcessWithTokenW.restype = BOOL
