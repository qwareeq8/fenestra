"""Explorer default folder view management.

The module makes Details view the default for every File Explorer folder type
with the same registry mechanism as LesFerch/WinSetView, reduced to one
opinionated action:

1. It backs up the affected registry keys to ``.reg`` files.
2. It deletes the per-folder view caches and saved defaults so stale state
   cannot shadow the new defaults.
3. It copies HKLM FolderTypes to HKCU and forces Details on every TopViews
   entry.
4. It writes Details-view bag entries for This PC, which has no FolderTypes
   GUID of its own.
5. It restarts Explorer so the running shell drops its cached view state.

The module separates pure plan construction (testable everywhere) from
the Windows-only executor (winreg is imported lazily so unit tests can
import this module on any platform).
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime

from fenestra.platform.win32_abi import (
    ADVAPI32,
    DWORD,
    HANDLE,
    KERNEL32,
    PROCESS_INFORMATION,
    SHELL32,
    STARTUPINFOW,
    USER32,
)
from fenestra.services.explorer_constants import FVM_DETAILS

LOG = logging.getLogger("Fenestra")

REG_EXPORT_TIMEOUT_SECONDS = 15
TASKKILL_TIMEOUT_SECONDS = 10
BACKUP_RETENTION_COUNT = 10
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_BACKUP_DIRECTORY_PATTERN = re.compile(r"view-backup-\d{8}-\d{6}-\d{6}(?:-\d{2})?\Z")

# Registry paths, all relative to HKCU unless noted otherwise.
SHELL_CLASSES = r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell"
BAGS_KEY = SHELL_CLASSES + r"\Bags"
BAGMRU_KEY = SHELL_CLASSES + r"\BagMRU"
SHELL_LEGACY = r"Software\Microsoft\Windows\Shell"
BAGS_LEGACY_KEY = SHELL_LEGACY + r"\Bags"
BAGMRU_LEGACY_KEY = SHELL_LEGACY + r"\BagMRU"
STREAMS_DEFAULTS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Streams\Defaults"
FOLDER_TYPES_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FolderTypes"

# This PC has no FolderTypes entry; its view lives in a numbered bag.
THIS_PC_GUID = "{5C4F28B5-F869-4E84-8E60-F11DB97C5CC7}"
THIS_PC_PIDL = bytes.fromhex("14001F50E04FD020EA3A6910A2D808002B30309D0000")

# Details view constants (WinSetView SetViewValues index 1).
LOGICAL_VIEW_MODE_DETAILS = 1
MODE_DETAILS = FVM_DETAILS
ICON_SIZE_DETAILS = 16
BAG_FFLAGS = 0x41200001
GROUP_BY_FMTID = "{B725F130-47EF-101A-A5F1-02608C9EEBAC}"
GROUP_BY_PID = 4

# Keys removed when clearing cached view state (HKCU-relative).
VIEW_CACHE_KEYS = (
    BAGMRU_KEY,
    BAGS_KEY,
    BAGMRU_LEGACY_KEY,
    BAGS_LEGACY_KEY,
    STREAMS_DEFAULTS_KEY,
    FOLDER_TYPES_KEY,
)

# Keys exported to the backup before anything is modified.
BACKUP_KEYS = VIEW_CACHE_KEYS

# Explorer keeps the desktop's icon positions, icon size, and sort order in
# this bag. Clearing the view cache must not rearrange the desktop.
DESKTOP_BAG_KEY = BAGS_LEGACY_KEY + r"\1\Desktop"

# The root of the legacy BagMRU assigns the desktop its bag slot. Without it,
# Explorer claims slot 1 afresh and overwrites the kept desktop bag with the
# Windows defaults, so these root values survive a cleared cache too.
DESKTOP_SLOT_VALUES = ("NodeSlot", "NodeSlots")
EMPTY_MRU_LIST = b"\xff\xff\xff\xff"


@dataclass(frozen=True)
class RegValue:
    """One registry value write, relative to HKCU."""

    key: str
    name: str
    kind: str  # The supported kinds are "dword", "sz", and "binary".
    data: int | str | bytes


def this_pc_bag_values() -> list[RegValue]:
    """Build the value writes that force Details view for This PC."""
    values = [
        RegValue(BAGMRU_KEY, "NodeSlots", "binary", b"\x02"),
        RegValue(BAGMRU_KEY, "MRUListEx", "binary", bytes.fromhex("00000000ffffffff")),
        RegValue(BAGMRU_KEY, "0", "binary", THIS_PC_PIDL),
        RegValue(BAGMRU_KEY + r"\0", "NodeSlot", "dword", 1),
    ]
    for bag in (
        rf"{BAGS_KEY}\1\Shell\{THIS_PC_GUID}",
        rf"{BAGS_KEY}\1\ComDlg\{THIS_PC_GUID}",
    ):
        values.extend(
            [
                RegValue(bag, "FFlags", "dword", BAG_FFLAGS),
                RegValue(bag, "LogicalViewMode", "dword", LOGICAL_VIEW_MODE_DETAILS),
                RegValue(bag, "Mode", "dword", MODE_DETAILS),
                RegValue(bag, "GroupView", "dword", 1),
                RegValue(bag, "IconSize", "dword", ICON_SIZE_DETAILS),
                RegValue(bag, "GroupByKey:FMTID", "sz", GROUP_BY_FMTID),
                RegValue(bag, "GroupByKey:PID", "dword", GROUP_BY_PID),
            ]
        )
    return values


def top_view_values(top_view_key: str) -> list[RegValue]:
    """Build the value writes that force Details on one TopViews entry."""
    return [
        RegValue(top_view_key, "LogicalViewMode", "dword", LOGICAL_VIEW_MODE_DETAILS),
        RegValue(top_view_key, "IconSize", "dword", ICON_SIZE_DETAILS),
    ]


def backup_dir_name(now: datetime) -> str:
    """Return the timestamped directory name for a registry backup."""
    return now.strftime("view-backup-%Y%m%d-%H%M%S-%f")


# ----------------------------------------------------------------------------
# The Windows-only executor imports ``winreg`` lazily for cross-platform imports.
# ----------------------------------------------------------------------------


def _open_winreg():
    """Import and return the Windows registry module lazily."""
    import winreg

    return winreg


def _system32_directory() -> str:
    """Return the trusted native Windows system directory."""
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(system_root, "System32")


def _system32_executable(name: str) -> str:
    """Resolve a Windows utility without PATH/current-directory search."""
    return os.path.join(_system32_directory(), name)


def _registry_error_code(exc: OSError) -> int | None:
    """Return a Windows registry error code from real or test exceptions."""
    code = getattr(exc, "winerror", None)
    if isinstance(code, int):
        return code
    if exc.args and isinstance(exc.args[0], int):
        return exc.args[0]
    return None


def _is_missing_registry_key(exc: OSError) -> bool:
    """Return whether an exception reports a missing registry key."""
    return isinstance(exc, FileNotFoundError) or _registry_error_code(exc) in (2, 3)


def _is_registry_enumeration_end(exc: OSError) -> bool:
    """Return whether registry enumeration reached the end of its values."""
    return _registry_error_code(exc) == 259


def _delete_key_tree(winreg, root, path: str) -> None:
    """Recursively delete a registry key. Missing keys are not an error."""
    access = winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_64KEY
    try:
        key = winreg.OpenKey(root, path, 0, access)
    except OSError as exc:
        if _is_missing_registry_key(exc):
            return
        raise
    children = []
    try:
        index = 0
        while True:
            try:
                children.append(winreg.EnumKey(key, index))
            except OSError as exc:
                if _is_registry_enumeration_end(exc):
                    break
                raise
            index += 1
    finally:
        key.Close()
    for child in children:
        _delete_key_tree(winreg, root, path + "\\" + child)
    try:
        winreg.DeleteKeyEx(root, path, winreg.KEY_WOW64_64KEY, 0)
    except OSError as exc:
        if _is_missing_registry_key(exc):
            return
        raise RuntimeError(f"Could not delete registry key HKCU\\{path}.") from exc


def _copy_key_tree(winreg, src_root, src_path: str, dst_root, dst_path: str) -> None:
    """Recursively copy a registry key tree, preserving value types."""
    read = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
    write = winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY
    with winreg.OpenKey(src_root, src_path, 0, read) as src:
        with winreg.CreateKeyEx(dst_root, dst_path, 0, write) as dst:
            index = 0
            while True:
                try:
                    name, data, kind = winreg.EnumValue(src, index)
                except OSError as exc:
                    if _is_registry_enumeration_end(exc):
                        break
                    raise
                winreg.SetValueEx(dst, name, 0, kind, data)
                index += 1
        index = 0
        while True:
            try:
                child = winreg.EnumKey(src, index)
            except OSError as exc:
                if _is_registry_enumeration_end(exc):
                    break
                raise
            _copy_key_tree(
                winreg, src_root, src_path + "\\" + child, dst_root, dst_path + "\\" + child
            )
            index += 1


def _read_key_values(winreg, path: str) -> list[tuple[str, object, int]] | None:
    """Return every value of an HKCU key, or None when the key is missing."""
    access = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, access)
    except OSError as exc:
        if _is_missing_registry_key(exc):
            return None
        raise
    values = []
    try:
        index = 0
        while True:
            try:
                values.append(winreg.EnumValue(key, index))
            except OSError as exc:
                if _is_registry_enumeration_end(exc):
                    break
                raise
            index += 1
    finally:
        key.Close()
    return values


def _clear_view_cache(winreg) -> None:
    """Delete every cached folder view while keeping the desktop layout."""
    desktop = _read_key_values(winreg, DESKTOP_BAG_KEY)
    slot = [
        value
        for value in _read_key_values(winreg, BAGMRU_LEGACY_KEY) or []
        if value[0] in DESKTOP_SLOT_VALUES
    ]
    for key in VIEW_CACHE_KEYS:
        _delete_key_tree(winreg, winreg.HKEY_CURRENT_USER, key)
    if desktop is None:
        return
    access = winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY
    if slot:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, BAGMRU_LEGACY_KEY, 0, access) as key:
            for name, data, kind in slot:
                winreg.SetValueEx(key, name, 0, kind, data)
            # The folders the list named were deleted with the cache.
            winreg.SetValueEx(key, "MRUListEx", 0, winreg.REG_BINARY, EMPTY_MRU_LIST)
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, DESKTOP_BAG_KEY, 0, access) as key:
        for name, data, kind in desktop:
            winreg.SetValueEx(key, name, 0, kind, data)


def _failure_result(error: Exception, backup: str | None) -> dict:
    """Return a failed bridge result that names the recovery backup, if any."""
    message = str(error).rstrip(".") + "."
    if backup:
        message += f" The previous folder views are saved in {backup}."
    return {"ok": False, "error": message}


def _write_values(winreg, values: list[RegValue]) -> None:
    """Apply a list of RegValue writes under HKCU."""
    kinds = {
        "dword": winreg.REG_DWORD,
        "sz": winreg.REG_SZ,
        "binary": winreg.REG_BINARY,
    }
    access = winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY
    for value in values:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, value.key, 0, access) as key:
            winreg.SetValueEx(key, value.name, 0, kinds[value.kind], value.data)


def _force_details_on_folder_types(winreg) -> int:
    """Force Details on every TopViews entry of the HKCU FolderTypes copy.

    Returns the number of TopViews entries updated.
    """
    read = winreg.KEY_READ | winreg.KEY_WOW64_64KEY
    updated = 0
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, FOLDER_TYPES_KEY, 0, read) as folder_types:
        index = 0
        while True:
            try:
                type_guid = winreg.EnumKey(folder_types, index)
            except OSError as exc:
                if _is_registry_enumeration_end(exc):
                    break
                raise
            index += 1
            top_views = rf"{FOLDER_TYPES_KEY}\{type_guid}\TopViews"
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, top_views, 0, read) as views:
                    view_index = 0
                    view_guids = []
                    while True:
                        try:
                            view_guids.append(winreg.EnumKey(views, view_index))
                        except OSError as exc:
                            if _is_registry_enumeration_end(exc):
                                break
                            raise
                        view_index += 1
            except OSError as exc:
                if _is_missing_registry_key(exc):
                    continue
                raise
            for view_guid in view_guids:
                _write_values(winreg, top_view_values(rf"{top_views}\{view_guid}"))
                updated += 1
    return updated


def _key_exists(winreg, key: str) -> bool:
    try:
        winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        ).Close()
        return True
    except OSError as exc:
        if _is_missing_registry_key(exc):
            return False
        raise


def _prune_registry_backups(
    base: str,
    keep: int = BACKUP_RETENTION_COUNT,
    *,
    protected: str | None = None,
) -> None:
    """Keep the newest matching backups while preserving the just-created backup."""
    if keep < 1:
        raise ValueError("Backup retention must keep at least one directory.")
    try:
        entries = list(os.scandir(base))
    except FileNotFoundError:
        return
    except OSError:
        LOG.warning("Folder-view backup retention could not inspect %s.", base, exc_info=True)
        return

    candidates = []
    for entry in entries:
        try:
            if not _BACKUP_DIRECTORY_PATTERN.fullmatch(entry.name):
                continue
            if entry.is_symlink() or (
                hasattr(os.path, "isjunction") and os.path.isjunction(entry.path)
            ):
                continue
            if entry.is_dir(follow_symlinks=False):
                candidates.append(entry)
        except OSError:
            LOG.warning(
                "Folder-view backup retention could not inspect %s.", entry.path, exc_info=True
            )

    resolved_base = os.path.realpath(os.path.abspath(base))
    candidates.sort(key=lambda entry: entry.name, reverse=True)
    # The oldest backup holds the views from before Fenestra first changed them,
    # so it survives alongside the newest ones.
    kept = [*candidates[: keep - 1], *candidates[-1:]] if len(candidates) > keep else candidates
    retained = {os.path.normcase(os.path.abspath(entry.path)) for entry in kept}
    protected_path = os.path.normcase(os.path.abspath(protected)) if protected else None
    candidate_paths = {os.path.normcase(os.path.abspath(entry.path)) for entry in candidates}
    if protected_path in candidate_paths and protected_path not in retained:
        # Give up the newest slot rather than the original backup.
        retained.discard(os.path.normcase(os.path.abspath(kept[0].path)))
        retained.add(protected_path)

    for entry in candidates:
        entry_path = os.path.normcase(os.path.abspath(entry.path))
        if entry_path in retained:
            continue
        try:
            resolved_entry = os.path.realpath(os.path.abspath(entry.path))
            if os.path.commonpath((resolved_base, resolved_entry)) != resolved_base:
                LOG.warning("Folder-view backup retention rejected path %s.", entry.path)
                continue
            shutil.rmtree(entry.path)
        except (OSError, ValueError):
            LOG.warning(
                "Folder-view backup retention could not remove %s.", entry.path, exc_info=True
            )


def _backup_registry_state() -> str:
    """Export the affected keys to ``.reg`` files and return the backup directory.

    Raises ``RuntimeError`` if an existing key cannot be exported, so the caller
    does not destroy view state it has no recovery backup for.
    """
    winreg = _open_winreg()
    base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Fenestra")
    backup_name = backup_dir_name(datetime.now())
    os.makedirs(base, exist_ok=True)
    # An interrupted export must never look like a complete retained backup.
    # Publish the final name atomically only after every existing key succeeds.
    staging = tempfile.mkdtemp(prefix=f".{backup_name}-staging-", dir=base)
    try:
        for index, key in enumerate(BACKUP_KEYS):
            out_file = os.path.join(staging, f"{index:02d}-{key.rsplit(chr(92), 1)[-1]}.reg")
            try:
                result = subprocess.run(
                    [
                        _system32_executable("reg.exe"),
                        "export",
                        "HKCU\\" + key,
                        out_file,
                        "/y",
                        "/reg:64",
                    ],
                    capture_output=True,
                    creationflags=_CREATE_NO_WINDOW,
                    check=False,
                    timeout=REG_EXPORT_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"Backup of HKCU\\{key} timed out after {REG_EXPORT_TIMEOUT_SECONDS} seconds."
                ) from exc
            if result.returncode != 0:
                if _key_exists(winreg, key):
                    stderr = result.stderr.decode("utf-8", "replace").strip()
                    raise RuntimeError(
                        f"Backup of existing key HKCU\\{key} failed: "
                        f"{stderr or 'reg.exe reported an error.'}"
                    )
                # Key does not exist yet; nothing to back up.
                LOG.info("Backup skipped for missing key HKCU\\%s.", key)

        suffix = 0
        while True:
            candidate_name = backup_name if suffix == 0 else f"{backup_name}-{suffix:02d}"
            target = os.path.join(base, candidate_name)
            suffix += 1
            if os.path.lexists(target):
                continue
            try:
                # This is an atomic, no-replace publication on supported Windows hosts.
                os.rename(staging, target)
                break
            except OSError:
                # Another process may have published this timestamp between the
                # existence check and rename. Retry only when that target now exists.
                if os.path.lexists(target):
                    continue
                raise
    finally:
        if os.path.lexists(staging):
            try:
                shutil.rmtree(staging)
            except OSError:
                LOG.warning("Could not remove incomplete folder-view backup %s.", staging)
    # Prune only after every existing registry key was exported successfully.
    # Exact-name filtering and reparse-point rejection keep deletion confined
    # to Fenestra-owned backup directories.
    _prune_registry_backups(base, protected=target)
    return target


def _shell_token():
    """Return a duplicate shell token for launching unelevated Explorer.

    ``None`` indicates that no usable shell token was available.
    """
    hwnd = USER32.GetShellWindow()
    if not hwnd:
        return None
    pid = DWORD()
    USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    process = KERNEL32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return None
    try:
        TOKEN_DUPLICATE = 0x0002
        TOKEN_QUERY = 0x0008
        token = HANDLE()
        if not ADVAPI32.OpenProcessToken(
            process, TOKEN_DUPLICATE | TOKEN_QUERY, ctypes.byref(token)
        ):
            return None
        try:
            MAXIMUM_ALLOWED = 0x02000000
            SECURITY_IMPERSONATION = 2
            TOKEN_PRIMARY = 1
            primary = HANDLE()
            if not ADVAPI32.DuplicateTokenEx(
                token,
                MAXIMUM_ALLOWED,
                None,
                SECURITY_IMPERSONATION,
                TOKEN_PRIMARY,
                ctypes.byref(primary),
            ):
                return None
            return primary
        finally:
            KERNEL32.CloseHandle(token)
    finally:
        KERNEL32.CloseHandle(process)


def _shell_process_id() -> int:
    """Return the desktop shell process ID, or zero when no shell exists."""
    hwnd = USER32.GetShellWindow()
    if not hwnd:
        return 0
    pid = DWORD()
    USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _process_session_id(process_id: int) -> int | None:
    """Return the Windows session containing a process, or None on failure."""
    session_id = DWORD()
    if not KERNEL32.ProcessIdToSessionId(DWORD(process_id), ctypes.byref(session_id)):
        return None
    return int(session_id.value)


def _wait_for_replacement_shell(previous_pid: int, timeout: float) -> int:
    """Wait for a shell whose process differs from the terminated shell."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current_pid = _shell_process_id()
        if current_pid and current_pid != previous_pid:
            return current_pid
        time.sleep(0.1)
    return 0


def _create_process_with_token(token: HANDLE, application: str, command_line: str) -> bool:
    """Start one process under a duplicated non-elevated shell token."""
    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(STARTUPINFOW)
    info = PROCESS_INFORMATION()
    mutable_command = ctypes.create_unicode_buffer(command_line)
    launched = ADVAPI32.CreateProcessWithTokenW(
        token,
        0,
        application,
        mutable_command,
        _CREATE_NO_WINDOW,
        None,
        None,
        ctypes.byref(startup),
        ctypes.byref(info),
    )
    if launched:
        KERNEL32.CloseHandle(info.hProcess)
        KERNEL32.CloseHandle(info.hThread)
    return bool(launched)


def _schedule_shell_recovery(token: HANDLE | None, previous_pid: int, explorer: str) -> bool:
    """Start a recovery helper before killing the current desktop shell.

    The helper waits for the existing shell process to exit and only then
    starts Explorer if Windows has not already created a replacement. If
    termination fails, it observes the old PID still running and exits.
    Elevated callers provide a normal-integrity shell token; ordinary callers
    launch the same helper directly.
    """
    if not previous_pid:
        return False
    powershell = os.path.join(
        _system32_directory(),
        "WindowsPowerShell",
        "v1.0",
        "powershell.exe",
    )
    escaped_explorer = explorer.replace("'", "''")
    script = (
        "Add-Type -Name NativeMethods -Namespace Fenestra "
        '-MemberDefinition \'[System.Runtime.InteropServices.DllImport("user32.dll")] '
        "public static extern System.IntPtr GetShellWindow();'; "
        f"$p=Get-Process -Id {previous_pid} -ErrorAction SilentlyContinue; "
        f"if ($p) {{ Wait-Process -Id {previous_pid} -Timeout 20 "
        "-ErrorAction SilentlyContinue }; "
        f"if (-not (Get-Process -Id {previous_pid} -ErrorAction SilentlyContinue)) {{ "
        "$deadline=(Get-Date).AddSeconds(5); "
        "while ([Fenestra.NativeMethods]::GetShellWindow() -eq [IntPtr]::Zero "
        "-and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200 }; "
        "if ([Fenestra.NativeMethods]::GetShellWindow() -eq [IntPtr]::Zero) "
        f"{{ Start-Process -FilePath '{escaped_explorer}' }} }}"
    )
    command_line = subprocess.list2cmdline(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-Command",
            script,
        ]
    )
    if token is not None:
        return _create_process_with_token(token, powershell, command_line)
    try:
        subprocess.Popen(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                script,
            ],
            creationflags=_CREATE_NO_WINDOW,
            close_fds=True,
        )
    except OSError:
        LOG.exception("Could not start the Explorer recovery helper.")
        return False
    return True


def _is_elevated() -> bool:
    """Return whether the current process has an elevated Windows token."""
    try:
        return bool(SHELL32.IsUserAnAdmin())
    except Exception:
        return False


def _current_process_owns_shell() -> bool:
    """Return whether Fenestra and the desktop shell have the same user SID.

    Over-the-shoulder UAC can run Fenestra under an administrator's profile
    while Explorer belongs to a different standard user. HKCU writes in that
    state would silently modify the wrong account, so folder-view work must
    fail closed.
    """
    process = current_token = shell_token = None
    try:
        import win32api
        import win32con
        import win32security

        shell_pid = _shell_process_id()
        if not shell_pid:
            return False
        process = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, shell_pid)
        current_token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
        )
        shell_token = win32security.OpenProcessToken(process, win32con.TOKEN_QUERY)

        def user_sid(token) -> str:
            sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            return win32security.ConvertSidToStringSid(sid)

        return user_sid(current_token) == user_sid(shell_token)
    except Exception:
        LOG.exception("Could not verify the desktop shell account.")
        return False
    finally:
        for handle in (shell_token, current_token, process):
            if handle is not None:
                try:
                    handle.Close()
                except Exception:
                    pass


def _folder_view_account_error() -> dict | None:
    """Return a fail-closed result when Fenestra does not own the shell account."""
    if _current_process_owns_shell():
        return None
    return {
        "ok": False,
        "error": (
            "Folder views were not changed because Fenestra and File Explorer "
            "are running as different Windows accounts. Sign in with the "
            "administrator account instead of entering another account's UAC credentials."
        ),
    }


def restart_explorer() -> bool:
    """Kill and relaunch Explorer, de-elevating the new shell if possible.

    Returns ``True`` after Windows or the recovery helper creates a replacement
    desktop shell.

    When this process is elevated, it must not relaunch Explorer with
    our own token, or the whole desktop shell would run at high integrity.
    If de-elevation via the shell token is unavailable, we skip the restart
    entirely and let the caller tell the user to restart Explorer or sign
    out, rather than spawning an elevated shell.
    """
    elevated = _is_elevated()
    previous_pid = _shell_process_id()
    if not previous_pid:
        LOG.error("Skipping Explorer restart: the current desktop shell was not found.")
        return False
    session_id = _process_session_id(previous_pid)
    if session_id is None:
        LOG.error("Skipping Explorer restart: the desktop session could not be identified.")
        return False
    explorer = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")
    token = None
    if elevated:
        token = _shell_token()
        if token is None:
            LOG.warning(
                "Skipping Explorer restart: cannot de-elevate the new shell. "
                "The user must restart Explorer manually."
            )
            return False
    try:
        recovery_scheduled = _schedule_shell_recovery(token, previous_pid, explorer)
    except Exception:
        LOG.exception("Could not start the Explorer recovery helper.")
        recovery_scheduled = False
    finally:
        # The recovery process has duplicated the token. This process no
        # longer owns a reason to retain it across taskkill failures.
        if token is not None:
            KERNEL32.CloseHandle(token)
            token = None
    if not recovery_scheduled:
        LOG.warning("Skipping Explorer restart: the recovery helper could not start.")
        return False

    try:
        result = subprocess.run(
            [
                _system32_executable("taskkill.exe"),
                "/f",
                "/fi",
                f"SESSION eq {session_id}",
                "/im",
                "explorer.exe",
            ],
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
            timeout=TASKKILL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        LOG.error("Explorer restart aborted: taskkill timed out.")
        return False
    except OSError:
        LOG.exception("Explorer restart aborted: taskkill could not start.")
        return False
    if result.returncode not in (0, 128):
        LOG.warning("taskkill explorer.exe returned %s.", result.returncode)
        return False

    if _wait_for_replacement_shell(previous_pid, 15.0):
        return True
    LOG.error("The Explorer recovery helper did not restore the desktop shell.")
    return False


def apply_details_default() -> dict:
    """Make Details view the default for all folders. Restarts Explorer.

    Returns a bridge-style result dict.
    """
    account_error = _folder_view_account_error()
    if account_error:
        return account_error
    backup = None
    try:
        winreg = _open_winreg()
        backup = _backup_registry_state()
        _clear_view_cache(winreg)
        _copy_key_tree(
            winreg,
            winreg.HKEY_LOCAL_MACHINE,
            FOLDER_TYPES_KEY,
            winreg.HKEY_CURRENT_USER,
            FOLDER_TYPES_KEY,
        )
        updated = _force_details_on_folder_types(winreg)
        _write_values(winreg, this_pc_bag_values())

        restarted = restart_explorer()
        LOG.info(
            "Details view was applied to %d folder views; backup=%s; restart=%s.",
            updated,
            backup,
            restarted,
        )
        return {
            "ok": True,
            "data": {"updated": updated, "backup": backup, "restarted": restarted},
        }
    except Exception as e:
        LOG.exception("Applying Details as the default folder view failed; backup=%s.", backup)
        return _failure_result(e, backup)


def reset_folder_views() -> dict:
    """Remove all custom view state so Explorer returns to Windows defaults.

    Restarts Explorer. Returns a bridge-style result dict.
    """
    account_error = _folder_view_account_error()
    if account_error:
        return account_error
    backup = None
    try:
        winreg = _open_winreg()
        backup = _backup_registry_state()
        _clear_view_cache(winreg)
        restarted = restart_explorer()
        LOG.info("Folder views were reset to defaults; backup=%s; restart=%s.", backup, restarted)
        return {"ok": True, "data": {"backup": backup, "restarted": restarted}}
    except Exception as e:
        LOG.exception("Resetting folder views failed; backup=%s.", backup)
        return _failure_result(e, backup)
