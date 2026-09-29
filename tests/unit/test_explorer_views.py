"""Tests for the Explorer default-view plan builders (pure logic, no registry)."""

import os
import subprocess
from datetime import datetime
from types import SimpleNamespace

from fenestra.services import explorer_views
from fenestra.services.explorer_views import (
    BAGMRU_KEY,
    BAGS_KEY,
    FOLDER_TYPES_KEY,
    ICON_SIZE_DETAILS,
    LOGICAL_VIEW_MODE_DETAILS,
    MODE_DETAILS,
    STREAMS_DEFAULTS_KEY,
    THIS_PC_GUID,
    VIEW_CACHE_KEYS,
    _copy_key_tree,
    _delete_key_tree,
    _key_exists,
    _prune_registry_backups,
    backup_dir_name,
    this_pc_bag_values,
    top_view_values,
)


def test_details_constants_match_winsetview():
    """Details view is LogicalViewMode=1, Mode=4, IconSize=16 (WinSetView index 1)."""
    assert LOGICAL_VIEW_MODE_DETAILS == 1
    assert MODE_DETAILS == 4
    assert ICON_SIZE_DETAILS == 16


def test_view_cache_keys_cover_both_hive_locations():
    """The cache wipe must clear Bags/BagMRU in both registry locations plus Streams."""
    assert BAGS_KEY in VIEW_CACHE_KEYS
    assert BAGMRU_KEY in VIEW_CACHE_KEYS
    assert STREAMS_DEFAULTS_KEY in VIEW_CACHE_KEYS
    assert FOLDER_TYPES_KEY in VIEW_CACHE_KEYS
    legacy = [k for k in VIEW_CACHE_KEYS if k.startswith("Software\\Microsoft\\Windows\\Shell")]
    assert len(legacy) == 2


def test_top_view_values_force_details():
    """Each TopViews entry gets LogicalViewMode=Details and the Details icon size."""
    values = top_view_values(r"FolderTypes\{guid}\TopViews\{view}")
    by_name = {v.name: v for v in values}
    assert by_name["LogicalViewMode"].data == LOGICAL_VIEW_MODE_DETAILS
    assert by_name["LogicalViewMode"].kind == "dword"
    assert by_name["IconSize"].data == ICON_SIZE_DETAILS
    assert all(v.key == r"FolderTypes\{guid}\TopViews\{view}" for v in values)


def test_this_pc_bag_values_target_bag_one():
    """This PC gets NodeSlot 1 and Details bags under Bags\\1\\Shell and Bags\\1\\ComDlg."""
    values = this_pc_bag_values()
    keys = {v.key for v in values}
    assert rf"{BAGS_KEY}\1\Shell\{THIS_PC_GUID}" in keys
    assert rf"{BAGS_KEY}\1\ComDlg\{THIS_PC_GUID}" in keys
    node_slot = [v for v in values if v.name == "NodeSlot"]
    assert len(node_slot) == 1
    assert node_slot[0].data == 1
    assert node_slot[0].key == BAGMRU_KEY + r"\0"
    shell_bag = [
        v for v in values if v.key == rf"{BAGS_KEY}\1\Shell\{THIS_PC_GUID}" and v.name == "Mode"
    ]
    assert shell_bag[0].data == MODE_DETAILS


def test_this_pc_pidl_is_binary():
    """The BagMRU slot value 0 holds the This PC PIDL as raw bytes."""
    values = this_pc_bag_values()
    pidl = [v for v in values if v.key == BAGMRU_KEY and v.name == "0"]
    assert len(pidl) == 1
    assert pidl[0].kind == "binary"
    assert isinstance(pidl[0].data, bytes)
    assert pidl[0].data.startswith(bytes.fromhex("14001F50"))


def test_backup_dir_name_is_timestamped():
    """Backup directories sort chronologically and are unique below one second."""
    name = backup_dir_name(datetime(2026, 7, 16, 13, 5, 9, 123456))
    assert name == "view-backup-20260716-130509-123456"


def test_backup_retention_keeps_the_original_and_newest_and_ignores_unrelated_paths(tmp_path):
    """Retention keeps the first backup and the newest nine, and nothing unrelated is removed."""
    backup_names = [f"view-backup-202607{day:02d}-130509-123456" for day in range(1, 13)]
    for name in backup_names:
        (tmp_path / name).mkdir()
    unrelated = tmp_path / "view-backup-manual"
    unrelated.mkdir()
    matching_file = tmp_path / "view-backup-20260630-130509-123456"
    matching_file.write_text("not a directory", encoding="utf-8")

    _prune_registry_backups(str(tmp_path))

    remaining = {entry.name for entry in tmp_path.iterdir() if entry.is_dir()}
    assert remaining == {backup_names[0], *backup_names[-9:], unrelated.name}
    assert matching_file.is_file()


def test_backup_retention_rejects_candidate_resolving_outside_base(monkeypatch, tmp_path):
    """A reparse-style resolved path cannot escape the application data directory."""
    newest = tmp_path / "view-backup-20260712-130509-123456"
    escaped = tmp_path / "view-backup-20260701-130509-123456"
    newest.mkdir()
    escaped.mkdir()
    realpath = explorer_views.os.path.realpath

    def resolve(path):
        if explorer_views.os.path.abspath(path) == str(escaped):
            return str(tmp_path.parent / "outside" / escaped.name)
        return realpath(path)

    monkeypatch.setattr(explorer_views.os.path, "realpath", resolve)

    _prune_registry_backups(str(tmp_path), keep=1)

    assert escaped.is_dir()


def test_backup_retention_preserves_current_backup_across_clock_changes(tmp_path):
    """Future-dated existing directories cannot evict the backup just created."""
    current = tmp_path / "view-backup-20250101-010101-000001"
    current.mkdir()
    for day in range(1, 11):
        (tmp_path / f"view-backup-209901{day:02d}-010101-000001").mkdir()

    _prune_registry_backups(str(tmp_path), protected=str(current))

    assert current.is_dir()
    assert len([entry for entry in tmp_path.iterdir() if entry.is_dir()]) == 10


def test_registry_backup_is_published_only_after_every_export(monkeypatch, tmp_path):
    """Successful exports move one hidden staging directory to the final name atomically."""
    base = tmp_path / "Fenestra"
    keys = (r"Software\Fenestra\First", r"Software\Fenestra\Second")
    rename_calls = []
    real_rename = explorer_views.os.rename

    def export_key(command, **_kwargs):
        out_file = os.fspath(command[3])
        parent = os.path.dirname(out_file)
        assert os.path.basename(parent).startswith(".view-backup-")
        assert "-staging-" in os.path.basename(parent)
        assert not list(base.glob("view-backup-*"))
        with open(out_file, "wb") as stream:
            stream.write(b"Windows Registry Editor Version 5.00\r\n")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    def rename(source, target):
        rename_calls.append((source, target))
        return real_rename(source, target)

    monkeypatch.setenv("LOCALAPPDATA", os.fspath(tmp_path))
    monkeypatch.setattr(explorer_views, "BACKUP_KEYS", keys)
    monkeypatch.setattr(explorer_views, "_open_winreg", object)
    monkeypatch.setattr(explorer_views.subprocess, "run", export_key)
    monkeypatch.setattr(explorer_views.os, "rename", rename)

    target = explorer_views._backup_registry_state()

    target_path = os.path.abspath(target)
    assert len(rename_calls) == 1
    assert os.path.basename(rename_calls[0][0]).startswith(".view-backup-")
    assert os.path.abspath(rename_calls[0][1]) == target_path
    assert sorted(os.listdir(target_path)) == ["00-First.reg", "01-Second.reg"]
    assert [entry.name for entry in base.iterdir()] == [os.path.basename(target_path)]


def test_registry_backup_failure_removes_incomplete_staging_directory(monkeypatch, tmp_path):
    """A failed export leaves neither a retained backup nor a hidden staging directory."""
    import pytest

    base = tmp_path / "Fenestra"
    keys = (r"Software\Fenestra\First", r"Software\Fenestra\Second")
    call_count = 0

    def export_key(command, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            with open(command[3], "wb") as stream:
                stream.write(b"partial")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"access denied")

    monkeypatch.setenv("LOCALAPPDATA", os.fspath(tmp_path))
    monkeypatch.setattr(explorer_views, "BACKUP_KEYS", keys)
    monkeypatch.setattr(explorer_views, "_open_winreg", object)
    monkeypatch.setattr(explorer_views, "_key_exists", lambda _winreg, _key: True)
    monkeypatch.setattr(explorer_views.subprocess, "run", export_key)

    with pytest.raises(RuntimeError, match=r"Backup of existing key HKCU\\Software"):
        explorer_views._backup_registry_state()

    assert base.is_dir()
    assert list(base.iterdir()) == []


def test_windows_utilities_resolve_from_system32(monkeypatch):
    """Elevated registry and shell commands must not use PATH search."""
    monkeypatch.setenv("SystemRoot", r"D:\TrustedWindows")

    assert explorer_views._system32_executable("reg.exe") == os.path.join(
        r"D:\TrustedWindows", "System32", "reg.exe"
    )


def test_folder_view_update_fails_before_registry_access_for_another_account(monkeypatch):
    """Over-the-shoulder UAC must not write the administrator profile's HKCU."""
    monkeypatch.setattr(explorer_views, "_current_process_owns_shell", lambda: False)
    monkeypatch.setattr(
        explorer_views,
        "_open_winreg",
        lambda: (_ for _ in ()).throw(AssertionError("registry was opened")),
    )

    result = explorer_views.apply_details_default()

    assert not result["ok"]
    assert "different Windows accounts" in result["error"]


class _RegistryKey:
    def __init__(self, path):
        self.path = path

    def Close(self):
        """Match the subset of the winreg key protocol used by the deleter."""


class _RegistryWithUndeletableChild:
    KEY_ALL_ACCESS = 1
    KEY_WOW64_64KEY = 2

    def __init__(self):
        self.enum_calls = 0

    def OpenKey(self, root, path, reserved, access):
        return _RegistryKey(path)

    def EnumKey(self, key, index):
        self.enum_calls += 1
        if key.path == "parent" and index == 0:
            return "child"
        raise OSError(259, "No more data is available")

    def DeleteKeyEx(self, root, path, access, reserved):
        raise OSError("busy")


def test_delete_key_tree_fails_once_instead_of_livelocking():
    """An undeletable child should fail closed without repeating index zero."""
    import pytest

    registry = _RegistryWithUndeletableChild()

    with pytest.raises(RuntimeError, match=r"HKCU\\parent\\child"):
        _delete_key_tree(registry, object(), "parent")

    assert registry.enum_calls == 3


def test_delete_key_tree_does_not_treat_access_denied_as_missing():
    """Access errors must abort rather than silently leave a partial tree."""
    import pytest

    registry = _RegistryWithUndeletableChild()
    registry.OpenKey = lambda *args: (_ for _ in ()).throw(PermissionError("denied"))

    with pytest.raises(PermissionError, match="denied"):
        _delete_key_tree(registry, object(), "parent")


def test_key_exists_does_not_treat_access_denied_as_missing():
    """Backup checks must distinguish an unreadable key from an absent key."""
    import pytest

    registry = _RegistryWithUndeletableChild()
    registry.HKEY_CURRENT_USER = object()
    registry.KEY_READ = 4
    registry.OpenKey = lambda *args: (_ for _ in ()).throw(PermissionError("denied"))

    with pytest.raises(PermissionError, match="denied"):
        _key_exists(registry, "parent")


def test_copy_key_tree_propagates_enumeration_errors():
    """A transient source-enumeration error must not produce a partial copy."""
    import pytest

    class ContextKey(_RegistryKey):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.Close()

    class Registry(_RegistryWithUndeletableChild):
        KEY_READ = 4
        KEY_WRITE = 8

        def OpenKey(self, root, path, reserved, access):
            return ContextKey(path)

        def CreateKeyEx(self, root, path, reserved, access):
            return ContextKey(path)

        def EnumValue(self, key, index):
            raise PermissionError("enumeration denied")

    with pytest.raises(PermissionError, match="enumeration denied"):
        _copy_key_tree(Registry(), object(), "source", object(), "target")


def test_restart_detects_windows_auto_restart_before_launch(monkeypatch):
    """A replacement shell should prevent a redundant explicit launch."""
    monkeypatch.setattr(explorer_views, "_is_elevated", lambda: False)
    monkeypatch.setattr(explorer_views, "_shell_process_id", lambda: 100)
    monkeypatch.setattr(explorer_views, "_process_session_id", lambda pid: 2)
    monkeypatch.setattr(explorer_views, "_wait_for_replacement_shell", lambda pid, timeout: 200)
    monkeypatch.setattr(explorer_views, "_schedule_shell_recovery", lambda *args: True)
    monkeypatch.setattr(
        explorer_views.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0),
    )
    monkeypatch.setattr(explorer_views.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    assert explorer_views.restart_explorer()


def test_non_elevated_restart_prearms_recovery_before_termination(monkeypatch):
    """Ordinary callers also need a recovery process before force-killing Explorer."""
    calls = []
    monkeypatch.setattr(explorer_views, "_is_elevated", lambda: False)
    monkeypatch.setattr(explorer_views, "_shell_process_id", lambda: 100)
    monkeypatch.setattr(explorer_views, "_process_session_id", lambda pid: 2)
    monkeypatch.setattr(
        explorer_views,
        "_schedule_shell_recovery",
        lambda token, pid, path: calls.append(("schedule", token, pid)) or True,
    )
    monkeypatch.setattr(
        explorer_views.subprocess,
        "run",
        lambda *args, **kwargs: (
            calls.append(("kill", args[0])) or subprocess.CompletedProcess(args[0], 0)
        ),
    )
    monkeypatch.setattr(explorer_views.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(explorer_views, "_wait_for_replacement_shell", lambda pid, timeout: 200)

    assert explorer_views.restart_explorer()
    assert calls[0] == ("schedule", None, 100)
    assert calls[1][0] == "kill"
    assert calls[1][1] == [
        explorer_views._system32_executable("taskkill.exe"),
        "/f",
        "/fi",
        "SESSION eq 2",
        "/im",
        "explorer.exe",
    ]


def test_non_elevated_restart_aborts_when_recovery_cannot_start(monkeypatch):
    """A helper launch failure must leave the current desktop shell untouched."""
    monkeypatch.setattr(explorer_views, "_is_elevated", lambda: False)
    monkeypatch.setattr(explorer_views, "_shell_process_id", lambda: 100)
    monkeypatch.setattr(explorer_views, "_process_session_id", lambda pid: 2)
    monkeypatch.setattr(explorer_views, "_schedule_shell_recovery", lambda *args: False)
    monkeypatch.setattr(
        explorer_views.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("shell was killed")),
    )

    assert not explorer_views.restart_explorer()


def test_elevated_restart_schedules_recovery_before_termination(monkeypatch):
    """An elevated restart must have an unelevated recovery process first."""
    calls = []
    kernel32 = SimpleNamespace(CloseHandle=lambda token: calls.append(("close", token)))
    monkeypatch.setattr(explorer_views, "KERNEL32", kernel32)
    monkeypatch.setattr(explorer_views, "_is_elevated", lambda: True)
    monkeypatch.setattr(explorer_views, "_shell_process_id", lambda: 100)
    monkeypatch.setattr(explorer_views, "_process_session_id", lambda pid: 2)
    monkeypatch.setattr(explorer_views, "_shell_token", lambda: 900)
    monkeypatch.setattr(
        explorer_views,
        "_schedule_shell_recovery",
        lambda token, pid, path: calls.append(("schedule", token, pid)) or True,
    )
    monkeypatch.setattr(
        explorer_views.subprocess,
        "run",
        lambda *args, **kwargs: calls.append(("kill",)) or subprocess.CompletedProcess(args[0], 0),
    )
    monkeypatch.setattr(explorer_views.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(explorer_views, "_wait_for_replacement_shell", lambda pid, timeout: 200)

    assert explorer_views.restart_explorer()
    assert calls[0][:1] == ("schedule",)
    assert calls[1] == ("close", 900)
    assert calls[2] == ("kill",)


def test_elevated_restart_aborts_when_recovery_cannot_start(monkeypatch):
    """Explorer must remain running when the recovery prerequisite fails."""
    calls = []
    monkeypatch.setattr(
        explorer_views,
        "KERNEL32",
        SimpleNamespace(CloseHandle=lambda token: calls.append(("close", token))),
    )
    monkeypatch.setattr(explorer_views, "_is_elevated", lambda: True)
    monkeypatch.setattr(explorer_views, "_shell_process_id", lambda: 100)
    monkeypatch.setattr(explorer_views, "_process_session_id", lambda pid: 2)
    monkeypatch.setattr(explorer_views, "_shell_token", lambda: 900)
    monkeypatch.setattr(explorer_views, "_schedule_shell_recovery", lambda *args: False)
    monkeypatch.setattr(
        explorer_views.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("shell was killed")),
    )

    assert not explorer_views.restart_explorer()
    assert calls == [("close", 900)]


def test_restart_fails_closed_without_a_desktop_shell(monkeypatch):
    """A zero shell PID cannot race a newly launched shell against taskkill."""
    monkeypatch.setattr(explorer_views, "_is_elevated", lambda: False)
    monkeypatch.setattr(explorer_views, "_shell_process_id", lambda: 0)
    monkeypatch.setattr(
        explorer_views,
        "_schedule_shell_recovery",
        lambda *args: (_ for _ in ()).throw(AssertionError("helper started")),
    )

    assert not explorer_views.restart_explorer()


def test_recovery_helper_polls_for_the_desktop_shell(monkeypatch):
    """The helper waits for Windows auto-restart before launching Explorer."""
    captured = []
    monkeypatch.setattr(
        explorer_views,
        "_create_process_with_token",
        lambda token, application, command: captured.append(command) or True,
    )
    monkeypatch.setattr(explorer_views.subprocess, "CREATE_NO_WINDOW", 0, raising=False)

    assert explorer_views._schedule_shell_recovery(900, 100, r"C:\Windows\explorer.exe")
    assert "GetShellWindow" in captured[0]
    assert "Timeout 20" in captured[0]
    assert "AddSeconds(5)" in captured[0]
    assert "Get-Process -Name explorer" not in captured[0]


def test_taskkill_start_failure_closes_the_shell_token(monkeypatch):
    """A duplicated shell token is released before any taskkill error path."""
    calls = []
    monkeypatch.setattr(
        explorer_views,
        "KERNEL32",
        SimpleNamespace(CloseHandle=lambda token: calls.append(("close", token))),
    )
    monkeypatch.setattr(explorer_views, "_is_elevated", lambda: True)
    monkeypatch.setattr(explorer_views, "_shell_process_id", lambda: 100)
    monkeypatch.setattr(explorer_views, "_process_session_id", lambda pid: 2)
    monkeypatch.setattr(explorer_views, "_shell_token", lambda: 900)
    monkeypatch.setattr(explorer_views, "_schedule_shell_recovery", lambda *args: True)
    monkeypatch.setattr(
        explorer_views.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("missing taskkill")),
    )

    assert not explorer_views.restart_explorer()
    assert calls == [("close", 900)]


class _MemoryRegistry:
    """Dictionary-backed subset of winreg for HKCU key trees."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = KEY_WRITE = KEY_ALL_ACCESS = KEY_WOW64_64KEY = 0
    REG_BINARY = 3

    def __init__(self, keys):
        self.keys = {path: dict(values) for path, values in keys.items()}

    class _Key:
        def __init__(self, path):
            self.path = path

        def Close(self):
            """Match the winreg handle protocol."""

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def OpenKey(self, _root, path, _reserved, _access):
        if path not in self.keys:
            raise FileNotFoundError(2, "missing")
        return self._Key(path)

    def CreateKeyEx(self, _root, path, _reserved, _access):
        self.keys.setdefault(path, {})
        return self._Key(path)

    def EnumValue(self, key, index):
        items = list(self.keys[key.path].items())
        if index >= len(items):
            raise OSError(259, "No more data is available")
        name, (data, kind) = items[index]
        return name, data, kind

    def EnumKey(self, key, index):
        prefix = key.path + "\\"
        children = sorted(
            {path[len(prefix) :].split("\\")[0] for path in self.keys if path.startswith(prefix)}
        )
        if index >= len(children):
            raise OSError(259, "No more data is available")
        return children[index]

    def SetValueEx(self, key, name, _reserved, kind, data):
        self.keys[key.path][name] = (data, kind)

    def DeleteKeyEx(self, _root, path, _access, _reserved):
        del self.keys[path]


def test_clearing_the_view_cache_keeps_the_desktop_icon_layout():
    """Folder-view resets do not rearrange the desktop."""
    desktop = explorer_views.DESKTOP_BAG_KEY
    registry = _MemoryRegistry(
        {
            explorer_views.BAGS_LEGACY_KEY: {},
            explorer_views.BAGS_LEGACY_KEY + "\\1": {},
            desktop: {"IconLayouts": (b"layout", 3)},
            explorer_views.BAGS_LEGACY_KEY + "\\2": {"Mode": (4, 4)},
            BAGMRU_KEY: {"NodeSlots": (b"\x02", 3)},
        }
    )

    explorer_views._clear_view_cache(registry)

    assert registry.keys == {desktop: {"IconLayouts": (b"layout", 3)}}


def test_clearing_the_view_cache_keeps_the_desktop_bag_slot():
    """Explorer still finds the kept desktop bag, so it keeps the icon size."""
    desktop = explorer_views.DESKTOP_BAG_KEY
    legacy_mru = explorer_views.BAGMRU_LEGACY_KEY
    registry = _MemoryRegistry(
        {
            explorer_views.BAGS_LEGACY_KEY: {},
            explorer_views.BAGS_LEGACY_KEY + "\\1": {},
            desktop: {"IconSize": (32, 4)},
            legacy_mru: {
                "NodeSlot": (1, 4),
                "NodeSlots": (b"\x02\x02", 3),
                "MRUListEx": (bytes.fromhex("00000000ffffffff"), 3),
                "0": (b"pidl", 3),
            },
            legacy_mru + "\\0": {"NodeSlot": (2, 4)},
        }
    )

    explorer_views._clear_view_cache(registry)

    assert registry.keys == {
        desktop: {"IconSize": (32, 4)},
        legacy_mru: {
            "NodeSlot": (1, 4),
            "NodeSlots": (b"\x02\x02", 3),
            "MRUListEx": (b"\xff\xff\xff\xff", 3),
        },
    }


def test_a_failed_folder_view_update_names_its_backup(monkeypatch):
    """A failure after the backup tells the user where the old views are."""
    monkeypatch.setattr(explorer_views, "_folder_view_account_error", lambda: None)
    monkeypatch.setattr(explorer_views, "_open_winreg", lambda: object())
    monkeypatch.setattr(explorer_views, "_backup_registry_state", lambda: "C:\\backup")

    def fail(_winreg):
        raise RuntimeError("Could not delete registry key HKCU\\Bags.")

    monkeypatch.setattr(explorer_views, "_clear_view_cache", fail)

    result = explorer_views.reset_folder_views()

    assert result == {
        "ok": False,
        "error": (
            "Could not delete registry key HKCU\\Bags. "
            "The previous folder views are saved in C:\\backup."
        ),
    }
