# Releasing

A release is an x64 installer built and verified on Windows from a commit on
`main`, and an annotated tag on that commit. The tag starts the CI workflow,
which repeats the source checks and builds and smoke-tests the installer on a
hosted Windows runner. Push a tag only after every local step below passes.

## Set the version

`APP_VERSION` in `fenestra/app/config.py` is the release version. Set the same
value in the frontend manifest and lockfile, then run the bootstrap again so it
reinstalls the project and the package metadata matches:

```powershell
$python = "C:\Path\To\Python313\python.exe"
$node = "C:\Path\To\nodejs\node.exe"
Push-Location frontend
npm version 0.0.2 --no-git-tag-version
Pop-Location
.\scripts\bootstrap.ps1 -PythonExecutable $python -NodeExecutable $node
```

Use Semantic Versioning. Version 0.0.1 is the first release; its tag must point
at the repository's only commit, `chore(repo): initial commit`.

## Build and verify

From a non-administrator PowerShell terminal at the repository root:

```powershell
.\scripts\build-installer.ps1 -PythonExecutable $python -NodeExecutable $node
.\scripts\verify-release.ps1
```

The build runs the frontend checks, a source smoke test, PyInstaller, a Qt
deployment audit, a PE architecture scan of the whole bundle, and a frozen smoke
test. It writes `dist\x64\Fenestra\` and
`installer\dist\FenestraSetup-<version>-x64.exe`, with evidence under
`build\x64\`. A zero exit code from PyInstaller or Inno Setup is not enough on
its own; any failed check blocks the release.

## Check the installed application

Install the built installer on the Windows machines you use, including a Windows
11 ARM64 machine if you have one, and confirm each item:

- Fenestra starts after one UAC prompt, and Task Manager reports its
  architecture as x64.
- The window, tray icon, tray menu, minimize-to-tray, and Quit behave as
  expected.
- Starting Fenestra again from the Start menu, while its window is hidden in the
  tray and while it is minimized, brings the running window to the front without
  a UAC prompt, and the tray menu's **Open** and minimize still work afterward.
- The window drags by its title bar, cannot be resized or maximized, and the
  snap gesture centers it while it stays movable.
- Snap and restore work on ordinary windows, on a window that refuses resizing,
  on monitors with different scaling, and on a monitor left of or above the
  primary display.
- Rebinding the snap key and saving keeps the new binding after a restart.
- Explorer autosizing fits Details columns in local, network, and cloud folders.
- **Make Details the default** and **Reset folder views** restart File Explorer
  and leave the desktop icons where they were.
- With **Launch at login** on, signing out and in starts Fenestra without a
  prompt, and uninstalling removes the logon task and the application files.

## Tag the release

Commit the version change with a Conventional Commit subject, push `main`, then
create and push an annotated tag named after the version:

```powershell
git tag -a v0.0.2 -m "Fenestra 0.0.2"
git push origin v0.0.2
```

CI refuses a tag that is not annotated, does not match `APP_VERSION`, or does
not point at the head of `main`. Never move or delete a pushed tag; fix a bad
release with a new patch version.
