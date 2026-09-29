# Fenestra project instructions

Use the canonical `claude-config/skills/core-skills` source. Apply
`code-standards`, `git-workflow`, and `packaging`, plus `gui-design` for
interface changes. Discover the local source location; do not assume an
installed skill copy is current.

Fenestra is a personal Windows tray utility: a Python and PySide6 backend that
hosts a React frontend in QWebEngineView and talks to it through one QWebChannel
bridge. [Development](docs/development.md) describes the setup, checks, and
architecture.

## Rules

- Every visible control must reach real backend behavior through the bridge. Do
  not add controls for features that do not exist.
- Keep `APP_VERSION` in `fenestra/app/config.py` as the only version source.
  `Fenestra.spec` parses it with a regular expression and must not import the
  package.
- Release payloads are x64 only. Prove architecture from PE headers, never from
  the host processor or `platform.machine()`, and never share an environment or
  `node_modules` tree across architectures.
- Check `$LASTEXITCODE` after every native command in PowerShell, because
  `$ErrorActionPreference = "Stop"` does not cover them.
- Treat a failed Qt hook or a missing `qwindows.dll`, `QtWebEngineProcess.exe`,
  or WebEngine resource as a failed build, whatever PyInstaller returns.
- Keep the Inno Setup version define behind `#ifndef` so `/D` can override it,
  and pass Vite `define` values through `JSON.stringify()`.
- Keep generated output out of Git: `build/`, `dist/`, `installer/dist/`,
  `frontend/dist/`, `.venv-x64/`, and `__pycache__/`.
- The application is Fenestra. Do not reintroduce its former names.

## Documentation and commits

Capitalize and punctuate every sentence in documentation, comments, docstrings,
interface copy, and test descriptions, and avoid dashes as sentence punctuation.
Python uses NumPy docstrings, JavaScript uses JSDoc, and PowerShell scripts use
comment-based help.

Use Conventional Commit subjects of at most 72 characters, and wrap capitalized,
punctuated body prose at 72 characters. Commits use Yusuf Qwareeq as both author
and committer, with no AI attribution: no `Co-Authored-By` or session trailers
and no generated-by notes in commits, tags, pull requests, or files. The release
source for 0.0.1 is one parentless commit on `main` with the subject
`chore(repo): initial commit` and an empty body. Commits, pushes, and tags
require explicit authorization, and a tag starts CI, so push one only after the
local release checks in [Releasing](docs/releasing.md) pass.
