"""Elevated logon-task autostart and relaunch command helpers."""

import os
import re
import shutil
import sys


def ensure_dispatch(app_name: str):
    """Create a COM dispatch and rebuild a corrupt ``gen_py`` cache once."""
    from win32com.client import Dispatch

    try:
        return Dispatch(app_name)
    except AttributeError:
        import logging

        logging.getLogger("Fenestra").warning(
            "win32com gen_py cache appears corrupted. Rebuilding."
        )
        module_names = [
            module.__name__ for module in sys.modules.values() if getattr(module, "__name__", None)
        ]
        for module_name in module_names:
            if re.match(r"win32com\.gen_py\..+", module_name):
                sys.modules.pop(module_name, None)

        localappdata = os.environ.get("LOCALAPPDATA")
        if localappdata:
            gen_py_path = os.path.join(localappdata, "Temp", "gen_py")
            if os.path.exists(gen_py_path):
                shutil.rmtree(gen_py_path, ignore_errors=True)

        from win32com import client

        return client.gencache.EnsureDispatch(app_name)


# Task Scheduler 2.0 constants used through the late-bound COM interface.
_TASK_TRIGGER_LOGON = 9
_TASK_ACTION_EXEC = 0
_TASK_CREATE_OR_UPDATE = 6
_TASK_LOGON_INTERACTIVE_TOKEN = 3
_TASK_RUNLEVEL_HIGHEST = 1
_TASK_ENUM_HIDDEN = 1
_NORMAL_PRIORITY = 4
_ERROR_TASK_NOT_FOUND = -2147024894  # HRESULT 0x80070002, "file not found".

STARTUP_TASK_NAME = "Fenestra"


def startup_task_name(user_sid: str) -> str:
    """Return the logon task name for one Windows user.

    Each user gets a task of their own, so enabling autostart for one user
    never replaces or removes another user's task.
    """
    return f"{STARTUP_TASK_NAME} {user_sid}"


def _current_user_sid() -> str:
    """Return the string SID of the user this process runs as."""
    import win32api
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    try:
        sid, _attributes = win32security.GetTokenInformation(token, win32security.TokenUser)
    finally:
        token.Close()
    return win32security.ConvertSidToStringSid(sid)


def _current_user_account() -> str:
    r"""Return the DOMAIN\user account name that scopes the logon trigger."""
    try:
        import win32api

        return win32api.GetUserNameEx(2)  # This is the NameSamCompatible format.
    except Exception:
        domain = os.environ.get("USERDOMAIN", "")
        user = os.environ.get("USERNAME", "")
        return f"{domain}\\{user}" if domain and user else user


def _task_scheduler_root(dispatch):
    """Connect to the Task Scheduler service and return it with its root folder."""
    service = dispatch("Schedule.Service")
    service.Connect()
    return service, service.GetFolder("\\")


def register_startup_task(
    target, arguments, working_directory, dispatch=None, user_sid=None
) -> None:
    """Register or update the elevated logon task for the current user.

    A highest-run-level logon task starts Fenestra elevated at sign-in without
    a UAC prompt, which a Startup-folder shortcut cannot do for an
    application that always self-elevates. Registration itself requires an
    elevated process.
    """
    dispatch = dispatch or ensure_dispatch
    service, root = _task_scheduler_root(dispatch)
    definition = service.NewTask(0)
    definition.RegistrationInfo.Description = "Starts Fenestra, elevated, when the user signs in."
    definition.Principal.RunLevel = _TASK_RUNLEVEL_HIGHEST
    definition.Principal.LogonType = _TASK_LOGON_INTERACTIVE_TOKEN
    settings = definition.Settings
    # A resident tray utility must start on battery power, and the scheduler's
    # default execution time limit must never stop it.
    settings.DisallowStartIfOnBatteries = False
    settings.StopIfGoingOnBatteries = False
    settings.ExecutionTimeLimit = "PT0S"
    # The scheduler's default priority 7 is below normal. Windows can remove
    # low-level keyboard hooks whose callbacks miss their timeout under load.
    settings.Priority = _NORMAL_PRIORITY
    trigger = definition.Triggers.Create(_TASK_TRIGGER_LOGON)
    trigger.UserId = _current_user_account()
    action = definition.Actions.Create(_TASK_ACTION_EXEC)
    action.Path = target
    if arguments:
        action.Arguments = arguments
    if working_directory:
        action.WorkingDirectory = working_directory
    root.RegisterTaskDefinition(
        startup_task_name(user_sid or _current_user_sid()),
        definition,
        _TASK_CREATE_OR_UPDATE,
        None,
        None,
        _TASK_LOGON_INTERACTIVE_TOKEN,
    )


def remove_startup_task(dispatch=None, user_sid=None) -> None:
    """Delete the current user's logon task. A missing task is not an error."""
    dispatch = dispatch or ensure_dispatch
    _service, root = _task_scheduler_root(dispatch)
    _delete_task(root, startup_task_name(user_sid or _current_user_sid()))


def remove_all_startup_tasks(dispatch=None) -> None:
    """Delete every user's logon task, as the uninstaller must."""
    dispatch = dispatch or ensure_dispatch
    _service, root = _task_scheduler_root(dispatch)
    names = [task.Name for task in root.GetTasks(_TASK_ENUM_HIDDEN)]
    for name in names:
        if name == STARTUP_TASK_NAME or name.startswith(f"{STARTUP_TASK_NAME} "):
            _delete_task(root, name)


def _delete_task(folder, name: str) -> None:
    """Delete one task, treating a task that is already gone as success."""
    try:
        folder.DeleteTask(name, 0)
    except Exception as error:
        if _is_missing_task_error(error):
            return
        raise


def _is_missing_task_error(error) -> bool:
    """Return whether a COM error reports the task as absent."""
    codes = set()
    hresult = getattr(error, "hresult", None)
    if isinstance(hresult, int):
        codes.add(hresult)
    excepinfo = getattr(error, "excepinfo", None)
    if excepinfo and len(excepinfo) >= 6 and isinstance(excepinfo[5], int):
        codes.add(excepinfo[5])
    return _ERROR_TASK_NOT_FOUND in codes or (_ERROR_TASK_NOT_FOUND & 0xFFFFFFFF) in codes


def select_pythonw_executable(executable, exists=os.path.exists):
    """Select a sibling ``pythonw.exe`` without assuming path-name casing."""
    if executable.lower().endswith("python.exe"):
        candidate = executable[:-10] + "pythonw.exe"
        if exists(candidate):
            return candidate
    return executable


def main_module_name(main_module=None) -> str | None:
    """Return the package that ``python -m`` started, or None for a script.

    Parameters
    ----------
    main_module : module, optional
        The ``__main__`` module to inspect. Defaults to the running one.

    Returns
    -------
    str or None
        The importable package name, or None when a script path started Python.
    """
    if main_module is None:
        main_module = sys.modules.get("__main__")
    spec = getattr(main_module, "__spec__", None)
    name = getattr(spec, "name", None)
    return name.removesuffix(".__main__") if name else None


def launch_command(executable, argv, *, frozen, main_module=None, exists=os.path.exists):
    """Return the program and arguments that start this application again.

    A package started with ``python -m`` must be restarted the same way.
    Running its ``__main__.py`` as a script puts the package directory first
    on ``sys.path``, where the ``platform`` subpackage shadows the standard
    library module.

    Parameters
    ----------
    executable : str
        The running interpreter or frozen executable.
    argv : list of str
        The argument vector to replay, starting with the program path.
    frozen : bool
        Whether the application runs as a PyInstaller executable.
    main_module : str, optional
        The package that ``python -m`` started, if any.
    exists : callable, optional
        The file-existence check used to find ``pythonw.exe``.

    Returns
    -------
    tuple of (str, list of str)
        The program to start and its arguments.
    """
    if frozen:
        return executable, list(argv[1:])
    program = select_pythonw_executable(executable, exists)
    if main_module:
        return program, ["-m", main_module, *argv[1:]]
    return program, [os.path.abspath(argv[0]), *argv[1:]]
