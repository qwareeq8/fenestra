# Troubleshooting

Fenestra writes its log to `%LOCALAPPDATA%\Fenestra\fenestra.log` and native
crash traces to `crash.log` beside it. Start with the log when anything below
does not match what you see.

## Running Fenestra

### A UAC prompt appears at every start

This is expected. The global keyboard hook and moves of other applications'
windows require elevation. Turn on **Launch at login** to start Fenestra
elevated at sign-in without a prompt. Approve the prompt with the account that
owns the desktop; Fenestra refuses to change folder views under a different
administrator's profile.

### The Sticky Keys prompt appears

Windows opens its Sticky Keys prompt after five Shift presses, so testing the
default three-press gesture repeatedly can trigger it. Rebind the snap key, or,
if you do not use Sticky Keys, turn off its keyboard shortcut in the Keyboard
page of the Windows Accessibility settings.

### Typing capitals or pressing Shift+Tab does not snap

This is intended. A press of any other key between snap-key taps starts the
count again, so Shift used as a modifier never adds up to a snap.

### A window does not move

Fenestra leaves minimized windows, shell windows, and, with **Game mode** on,
fullscreen windows alone. A window that is not responding is skipped with a
message instead of freezing Fenestra. A window that rejects a resize is centered
at its original size. Fenestra's own window keeps its fixed size, so the snap
gesture only centers it and the restore gesture leaves it where it is.

### Test snap reports that no window is eligible

Test snap moves the first window behind Fenestra. It skips minimized windows,
shell windows, and windows that Windows keeps hidden, such as suspended apps and
tool panels. Open or restore another window, then try again.

### Explorer columns are not autosized

Autosizing applies only to Details view. Switch the folder to Details with
Ctrl+Shift+6, or use **Make Details the default**, and confirm that **Auto-size
columns on folder change** is on.

### crash.log lists Windows fatal exception 0x800706ba

These entries are not crashes. When a folder-view action restarts File Explorer,
Windows reports that Explorer's COM server is unavailable (`0x800706ba`) while
autosizing reconnects, and the crash tracer records each report even though
Fenestra handles it.

### Folder views need to be restored

Every folder-view action first exports the affected registry keys to
`%LOCALAPPDATA%\Fenestra\view-backup-<timestamp>`. The oldest backup holds the
views from before Fenestra first changed them. Double-click its `.reg` files,
then sign out and in.

## Building Fenestra

### The scripts reject Python or Node.js

The build reads each executable's PE header and accepts only x64. On Windows 11
ARM64, `platform.machine()` can report ARM64 for an x64 process under emulation,
so rely on the script's verdict. Pass official x64 executables explicitly:

```powershell
.\scripts\bootstrap.ps1 -PythonExecutable C:\Path\To\Python313\python.exe -NodeExecutable C:\Path\To\nodejs\node.exe
```

Conda and Miniforge interpreters are rejected even inside a `venv`, because the
environment keeps their DLLs and ABI.

### The environment provenance does not match

`.venv-x64` records the interpreter and package set that created it. After a
change to `pyproject.toml` or `requirements/build-constraints.txt`, run the
bootstrap again with the same interpreter; it reinstalls into the environment
and records it again. If the bootstrap reports that the environment was not
created by the selected interpreter, or that a package is no longer constrained,
delete the environment and bootstrap again:

```powershell
Remove-Item -LiteralPath .venv-x64 -Recurse -Force
.\scripts\bootstrap.ps1 -PythonExecutable C:\Path\To\Python313\python.exe
```

### PyInstaller succeeds but the build fails

A failed Qt hook, such as
`QtLibraryInfo(PySide6): failed to obtain Qt library info`, or a missing
`qwindows.dll`, `QtWebEngineProcess.exe`, or WebEngine resource invalidates the
bundle even when PyInstaller exits with zero. Correct the import problem that
`.venv-x64\Scripts\python.exe -I -c "from PySide6 import QtWebEngineWidgets"`
reveals, then build again. Never copy Qt DLLs into the bundle by hand.

### npm audit reports a finding

The build fails on any advisory. Apply compatible updates with `npm audit fix`
and run the frontend checks; never use `--force` or disable the install-script
allowlist in `frontend/.npmrc`.

### Inno Setup is not found

Install Inno Setup 6.7.3, or set `ISCC_PATH` to its `ISCC.exe`. The build checks
the compiler version before it packages anything.
