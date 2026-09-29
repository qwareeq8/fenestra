"""Tests for autostart and relaunch helpers that do not require a live COM server."""

from __future__ import annotations

import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from fenestra.platform.startup import (
    ensure_dispatch,
    launch_command,
    main_module_name,
    select_pythonw_executable,
)


def test_select_pythonw_executable_preserves_mixed_case_prefix() -> None:
    """A mixed-case Python filename is replaced without corrupting its path."""
    executable = r"C:\Tools\Python312\PyThOn.ExE"

    selected = select_pythonw_executable(
        executable,
        exists=lambda path: path == r"C:\Tools\Python312\pythonw.exe",
    )

    assert selected == r"C:\Tools\Python312\pythonw.exe"


def test_select_pythonw_executable_keeps_original_when_sibling_is_missing() -> None:
    """Development startup remains usable when pythonw.exe is unavailable."""
    executable = r"C:\Tools\python.exe"
    assert select_pythonw_executable(executable, exists=lambda _path: False) == executable


def test_main_module_name_names_the_package_started_with_dash_m() -> None:
    """A ``python -m`` start is recognized from the main module's spec."""
    package_main = SimpleNamespace(__spec__=SimpleNamespace(name="fenestra.__main__"))
    script_main = SimpleNamespace(__spec__=None)

    assert main_module_name(package_main) == "fenestra"
    assert main_module_name(script_main) is None


def test_launch_command_replays_scripts_modules_and_frozen_builds() -> None:
    """Each start mode is restarted the way it was started."""
    exists = lambda path: path.endswith("pythonw.exe")  # noqa: E731
    script = launch_command(r"C:\Py\python.exe", ["main.py", "-x"], frozen=False, exists=exists)
    module = launch_command(
        r"C:\Py\python.exe", ["ignored"], frozen=False, main_module="fenestra", exists=exists
    )
    frozen = launch_command(r"C:\App\Fenestra.exe", ["Fenestra.exe", "-x"], frozen=True)

    assert script == (r"C:\Py\pythonw.exe", [os.path.abspath("main.py"), "-x"])
    assert module == (r"C:\Py\pythonw.exe", ["-m", "fenestra"])
    assert frozen == (r"C:\App\Fenestra.exe", ["-x"])


def _install_fake_win32com(monkeypatch, dispatch, ensure):
    client = types.ModuleType("win32com.client")
    client.Dispatch = dispatch
    client.gencache = types.SimpleNamespace(EnsureDispatch=ensure)
    win32com = types.ModuleType("win32com")
    win32com.client = client
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", client)


def test_ensure_dispatch_uses_normal_dispatch_when_cache_is_healthy(monkeypatch) -> None:
    """The normal path does not mutate or regenerate the COM cache."""
    expected = object()
    dispatch = MagicMock(return_value=expected)
    ensure = MagicMock()
    _install_fake_win32com(monkeypatch, dispatch, ensure)

    assert ensure_dispatch("WScript.Shell") is expected
    dispatch.assert_called_once_with("WScript.Shell")
    ensure.assert_not_called()


def test_ensure_dispatch_recovers_from_corrupt_generated_cache(monkeypatch, tmp_path) -> None:
    """An AttributeError purges generated wrappers and retries through gencache."""
    expected = object()
    dispatch = MagicMock(side_effect=AttributeError("corrupt wrapper"))
    ensure = MagicMock(return_value=expected)
    _install_fake_win32com(monkeypatch, dispatch, ensure)

    generated = types.ModuleType("win32com.gen_py.corrupt")
    monkeypatch.setitem(sys.modules, generated.__name__, generated)
    cache = tmp_path / "Temp" / "gen_py"
    cache.mkdir(parents=True)
    (cache / "broken.py").write_text("broken", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert ensure_dispatch("WScript.Shell") is expected
    assert generated.__name__ not in sys.modules
    assert not cache.exists()
    ensure.assert_called_once_with("WScript.Shell")


class _FakeTaskFolder:
    def __init__(self, names=()):
        self.registrations = []
        self.deleted = []
        self.delete_error = None
        self.names = list(names)

    def GetTasks(self, _flags):
        return [SimpleNamespace(Name=name) for name in self.names]

    def RegisterTaskDefinition(self, *args):
        self.registrations.append(args)

    def DeleteTask(self, name, flags):
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append((name, flags))


class _FakeTaskService:
    """Late-bound Schedule.Service stand-in that records the task definition."""

    def __init__(self, folder):
        self._folder = folder
        self.triggers = []
        self.actions = []
        self.definition = SimpleNamespace(
            RegistrationInfo=SimpleNamespace(Description=None),
            Principal=SimpleNamespace(RunLevel=None, LogonType=None),
            Settings=SimpleNamespace(
                DisallowStartIfOnBatteries=None,
                StopIfGoingOnBatteries=None,
                ExecutionTimeLimit=None,
                Priority=None,
            ),
            Triggers=SimpleNamespace(Create=self._create_trigger),
            Actions=SimpleNamespace(Create=self._create_action),
        )

    def _create_trigger(self, kind):
        trigger = SimpleNamespace(kind=kind, UserId=None)
        self.triggers.append(trigger)
        return trigger

    def _create_action(self, kind):
        action = SimpleNamespace(kind=kind, Path=None, Arguments=None, WorkingDirectory=None)
        self.actions.append(action)
        return action

    def Connect(self):
        pass

    def GetFolder(self, path):
        assert path == "\\"
        return self._folder

    def NewTask(self, _flags):
        return self.definition


def test_register_startup_task_builds_an_elevated_logon_task(monkeypatch) -> None:
    """Autostart registers a highest-run-level logon task for the current user."""
    from fenestra.platform import startup

    folder = _FakeTaskFolder()
    service = _FakeTaskService(folder)
    monkeypatch.setattr(startup, "_current_user_account", lambda: "DOMAIN\\user")

    startup.register_startup_task(
        r"C:\Apps\Fenestra\Fenestra.exe",
        "",
        r"C:\Apps\Fenestra",
        dispatch=lambda name: service,
        user_sid="S-1-5-21-1",
    )

    definition = service.definition
    assert definition.Principal.RunLevel == 1  # TASK_RUNLEVEL_HIGHEST
    assert definition.Principal.LogonType == 3  # TASK_LOGON_INTERACTIVE_TOKEN
    # A resident tray utility must start on battery and never be time-limited.
    assert definition.Settings.DisallowStartIfOnBatteries is False
    assert definition.Settings.StopIfGoingOnBatteries is False
    assert definition.Settings.ExecutionTimeLimit == "PT0S"
    # Below-normal priority lets Windows drop the global keyboard hooks.
    assert definition.Settings.Priority == 4
    assert [trigger.kind for trigger in service.triggers] == [9]  # TASK_TRIGGER_LOGON
    assert service.triggers[0].UserId == "DOMAIN\\user"
    action = service.actions[0]
    assert (action.kind, action.Path) == (0, r"C:\Apps\Fenestra\Fenestra.exe")
    assert action.WorkingDirectory == r"C:\Apps\Fenestra"
    name, registered, create_flags, _user, _password, logon_type = folder.registrations[0]
    assert (name, create_flags, logon_type) == ("Fenestra S-1-5-21-1", 6, 3)
    assert registered is definition


def test_remove_startup_task_tolerates_only_a_missing_task() -> None:
    """A missing task is not an uninstall failure, but other errors surface."""
    from fenestra.platform import startup

    class _NotFound(Exception):
        hresult = -2147024894  # HRESULT 0x80070002.

    class _Denied(Exception):
        hresult = -2147024891  # HRESULT 0x80070005.

    folder = _FakeTaskFolder()
    service = _FakeTaskService(folder)

    folder.delete_error = _NotFound()
    startup.remove_startup_task(dispatch=lambda name: service, user_sid="S-1-5-21-1")

    folder.delete_error = _Denied()
    with pytest.raises(_Denied):
        startup.remove_startup_task(dispatch=lambda name: service, user_sid="S-1-5-21-1")


def test_remove_startup_task_deletes_only_the_current_users_task() -> None:
    """One user turning autostart off leaves other users' tasks alone."""
    from fenestra.platform import startup

    folder = _FakeTaskFolder()
    startup.remove_startup_task(
        dispatch=lambda name: _FakeTaskService(folder), user_sid="S-1-5-21-2"
    )

    assert folder.deleted == [("Fenestra S-1-5-21-2", 0)]


def test_remove_all_startup_tasks_deletes_every_users_task_only() -> None:
    """Uninstall removes each user's task and leaves unrelated tasks alone."""
    from fenestra.platform import startup

    folder = _FakeTaskFolder(
        ["Fenestra S-1-5-21-1", "Fenestra S-1-5-21-2", "Fenestra", "FenestraOther", "Backup"]
    )
    startup.remove_all_startup_tasks(dispatch=lambda name: _FakeTaskService(folder))

    assert [name for name, _flags in folder.deleted] == [
        "Fenestra S-1-5-21-1",
        "Fenestra S-1-5-21-2",
        "Fenestra",
    ]


@pytest.mark.requires_qt
def test_sync_startup_task_registers_or_removes_the_logon_task(monkeypatch) -> None:
    """Enabling registers the elevated logon task; disabling removes it."""
    from fenestra.app import window as window_module

    registered = MagicMock()
    removed = MagicMock()
    monkeypatch.setattr(window_module, "register_startup_task", registered)
    monkeypatch.setattr(window_module, "remove_startup_task", removed)

    window_module.sync_startup_task(True)
    registered.assert_called_once()
    removed.assert_not_called()

    window_module.sync_startup_task(False)
    removed.assert_called_once()


def test_uninstall_task_helper_reports_missing_task_as_success(monkeypatch) -> None:
    """Task cleanup exits zero when the task is gone and one on a real failure."""
    from fenestra.app import __main__ as app_main
    from fenestra.platform import startup

    monkeypatch.setattr(startup, "remove_all_startup_tasks", MagicMock())
    assert app_main._remove_startup_task() == 0

    monkeypatch.setattr(
        startup, "remove_all_startup_tasks", MagicMock(side_effect=OSError("denied"))
    )
    assert app_main._remove_startup_task() == 1


@pytest.mark.requires_qt
def test_startup_reconciliation_reports_saved_but_unsynchronized_state(monkeypatch) -> None:
    """A failed logon-task update is reported accurately and retried on the next launch."""
    from fenestra.app import window as window_module

    monkeypatch.setattr(
        window_module,
        "sync_startup_task",
        MagicMock(side_effect=OSError("task scheduler is unavailable")),
    )
    main_window = window_module.MainWindow.__new__(window_module.MainWindow)
    main_window.settings = SimpleNamespace(run_at_startup=True)
    main_window._bridge = SimpleNamespace(snap_status=MagicMock())

    main_window._reconcile_startup_task()

    message, timeout, tone = main_window._bridge.snap_status.emit.call_args.args
    assert "setting is saved" in message
    assert "retry at the next launch" in message
    assert timeout == 8000
    assert tone == "error"
