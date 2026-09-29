# Development

Fenestra is a Python and PySide6 backend that hosts a React frontend in a
QWebEngineView. Development and release builds run on Windows with official x64
tools, because PyInstaller packages the interpreter and native wheels it runs
from and Qt WebEngine is published only for x64 Windows.

## Prerequisites

- Windows 10 version 1809 or later, on x64 or on Windows 11 ARM64 through x64
  emulation.
- Official x64 CPython 3.13 from
  [python.org](https://www.python.org/downloads/windows/). Conda and Miniforge
  interpreters are not supported.
- x64 Node.js 24 LTS with npm 11.
- Inno Setup 6.7.3, which only the installer build needs.

On a Windows 11 ARM64 computer, the default Python and Node.js installers are
ARM64. Install the x64 builds explicitly; the scripts read each executable's PE
header and refuse any other architecture.

## Set up

Run the bootstrap once from the repository root:

```powershell
$python = "C:\Path\To\Python313\python.exe"
$node = "C:\Path\To\nodejs\node.exe"
.\scripts\bootstrap.ps1 -PythonExecutable $python -NodeExecutable $node
```

The bootstrap creates `.venv-x64`, installs the project with its `dev` and
`build` extras under `requirements/build-constraints.txt` from PyPI, verifies
that every installed package matches its pin and that native wheels are x64, and
runs `npm ci` in `frontend/`. It records the interpreter and package set in a
provenance marker, and later builds refuse an environment that no longer matches
it. After changing `pyproject.toml` or the constraints, run the bootstrap again.
It checks only that `.venv-x64` runs the selected official interpreter, then
reinstalls the constrained packages into it and records the marker again. Delete
`.venv-x64` first when you switch interpreters or remove a package from the
constraints, because a reinstall neither changes the interpreter nor uninstalls
packages.

The bootstrap runs pip in isolated mode, so pip ignores its configuration files
and `PIP_*` environment variables and installs only from
`https://pypi.org/simple`. An extra index in your own configuration therefore
cannot supply a pinned package. If PyPI is unreachable, pass an HTTPS mirror
explicitly:

```powershell
.\scripts\bootstrap.ps1 -PythonExecutable $python -NodeExecutable $node -PackageIndexUrl https://mirror.example/simple
```

## Run in development mode

Start the Vite development server, then start Fenestra with `--dev` from a
second terminal:

```powershell
Push-Location frontend
npm run dev
```

```powershell
.venv-x64\Scripts\python.exe main.py --dev
```

The frontend reloads from `http://localhost:5173` as its sources change. The
`--dev` flag, or `FENESTRA_DEV=1`, survives the UAC relaunch. Add `--debug`, or
set `FENESTRA_DEBUG=1`, for verbose logging.

`npm run dev` on its own serves the interface in a browser with a mock bridge,
which is useful for layout work without Windows. The mock stages edits, saves
them, and emits the backend's signals. Query parameters choose its starting
appearance, for example
`http://localhost:5173/?theme=light&accent=teal&density=compact`; `theme` takes
`system`, `light`, or `dark`, `accent` takes `amber`, `green`, `teal`, `blue`,
or `violet`, and `density` takes `compact`, `cozy`, or `comfortable`. Only
development builds contain the mock and read these parameters.

## Check a change

Run the Python checks from the repository root:

```powershell
.venv-x64\Scripts\python.exe -m ruff format --check .
.venv-x64\Scripts\python.exe -m ruff check .
.venv-x64\Scripts\python.exe -m pytest -q
```

Run the frontend checks from `frontend/`:

```powershell
npm run format:check
npm run lint
npm test
npm run build
npm run visual:check
```

### Visual check

`npm run visual:check` starts the Vite development server and opens the
interface with Playwright's Chromium and the mock bridge. It captures the Window
snap page at its top and bottom, the Explorer, Shortcuts, General, and About
pages, the command palette, both confirmations, key capture, unsaved changes, a
footer error, a disabled Save button, and keyboard focus on a stepper button, a
segmented option, an accent swatch, a switch, the Close button, and a sidebar
item beside the current page. Every capture runs in the dark and light themes,
the three densities, the 860 by 600 minimum and 1000 by 620 default window
sizes, and device scale factors 1 and 2; a few captures also run in every
accent. The PNGs and one contact sheet per theme go to `build/visual/`.

Each capture must pass these checks, which fail with the element's name and the
measured values:

- A stepper's value, or value and unit together, is centered in its field within
  1 pixel.
- A button's label and shortcut hint share a vertical center within 1 pixel, and
  hint and keycap text is centered in its box within 1 pixel.
- No text is clipped or overflows its element, unless it shows an ellipsis.
- No two interactive elements overlap, and every focus ring is fully visible,
  with no window edge, clipping container, or neighboring control hiding it.
- Every interactive target is at least 24 by 24 pixels.
- axe-core reports no serious or critical violation, including contrast.

Glyph positions come from the rendered pixels, from the top of the ink to the
baseline, because the WebCM faces' vertical metrics differ from system fonts.
The check never compares screenshots with stored images, because fonts rasterize
differently on each host; review the captures by eye instead.

Install the browser once with `npx playwright install chromium`, or set
`FENESTRA_CHROMIUM` to an existing Chromium executable. Pass `-- --only=<text>`
to run only the variants whose names contain the text, such as
`-- --only=light-compact`, and `-- --update-docs` to also copy the dark Window
snap capture at scale factor 2 to `docs/images/window-snap-dark.png`. The
release workflow runs the check and uploads `build/visual/` as an artifact.

### Test environments

On Windows, pytest runs every test against the real Qt runtime. Elsewhere, the
test configuration stubs the Windows and Qt modules and skips the tests marked
`requires_qt`. Ruff enforces NumPy-style docstrings outside the tests, and every
exported frontend function carries JSDoc.

## Architecture

| Path                | Contents                                                             |
| ------------------- | -------------------------------------------------------------------- |
| `main.py`           | The source entry point, which calls `fenestra.app.main()`.           |
| `fenestra/app`      | Startup, elevation, the frameless window, hit testing, and web view. |
| `fenestra/bridge`   | The QWebChannel bridge object and the key-capture guard.             |
| `fenestra/services` | Snap and restore, Explorer column autosizing, and folder views.      |
| `fenestra/workers`  | The background key-capture and Explorer-autosize workers.            |
| `fenestra/platform` | Win32 declarations, logon-task autostart, paths, and theme.          |
| `fenestra/settings` | QSettings persistence, key validation, and the draft model.          |
| `frontend/src`      | The React 19 settings interface.                                     |
| `scripts`           | The PowerShell build pipeline and its Python verifiers.              |

The backend owns every operating-system operation: the global keyboard hook,
window moves, COM automation of Explorer, the tray icon, autostart, and
settings. The frontend renders settings and sends every change through one
`FenestraBridge` object as JSON strings. Edits are staged as a draft that the
backend previews live, and Save commits the draft to QSettings. Settings
payloads also carry the saved values, so indicators such as the sidebar's "On"
badge describe the settings in effect rather than the draft. The backend sends
each status message with an explicit tone, `neutral`, `success`, or `error`, and
the footer styles the message by that tone.

`APP_VERSION` in `fenestra/app/config.py` is the only version source. Python
packaging reads it, the frontend receives it through Vite's `__APP_VERSION__`
define, and the installer receives it through ISCC's `/D` flag.

## Branding

`branding/fenestra-icon.svg` and the 16-pixel `branding/fenestra-icon-small.svg`
are the sources of `icon.ico` and the installer bitmaps. Regenerate them after
changing either source. The helper's packages stay out of `.venv-x64`, whose
package set is pinned:

```powershell
& $python -m venv build\branding-env
build\branding-env\Scripts\python.exe -m pip install pillow resvg-py
build\branding-env\Scripts\python.exe scripts\build_branding.py
```
