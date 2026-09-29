"""Tests for how the bootstrap treats an existing environment.

The build scripts run only on Windows, but PowerShell 7 can dot-source the
shared helpers anywhere, so these tests replace the Windows-only probes with
stand-ins and run the helpers on any host that has ``pwsh``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
PWSH = shutil.which("pwsh")

requires_pwsh = pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is not installed.")


def _run_interpreter_check(venv_prefix: str, venv_version: str) -> subprocess.CompletedProcess:
    """Run Assert-FenestraEnvironmentInterpreter against a stand-in environment."""
    script = f"""
$ErrorActionPreference = "Stop"
. "{SCRIPTS / "build-common.ps1"}"
function Get-FenestraPythonProcessArchitecture {{
    param([string] $PythonExecutable)
    [pscustomobject]@{{
        architecture = "x64"
        version      = "{venv_version}"
        base_prefix  = "{venv_prefix}"
    }}
}}
$identity = [pscustomobject]@{{
    executable = "/opt/python313/python.exe"
    version    = "3.13.5"
    basePrefix = "/opt/python313"
}}
Assert-FenestraEnvironmentInterpreter `
    -Architecture x64 `
    -VenvPython "/work/.venv-x64/Scripts/python.exe" `
    -BaseIdentity $identity
"interpreter matches"
"""
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )


@requires_pwsh
def test_existing_environment_from_the_selected_interpreter_is_reused() -> None:
    """Changed dependency metadata does not matter; only the interpreter is checked."""
    result = _run_interpreter_check("/opt/python313", "3.13.5")

    assert result.returncode == 0, result.stderr
    assert "interpreter matches" in result.stdout


@requires_pwsh
def test_existing_environment_from_another_interpreter_names_the_remedy() -> None:
    """An environment made by a different interpreter must be recreated."""
    result = _run_interpreter_check("/opt/python312", "3.12.9")

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "was not created by the selected interpreter" in output
    assert "Python version" in output
    assert "Remove-Item" in output
    assert "bootstrap.ps1" in output


def test_bootstrap_reinstalls_instead_of_asserting_full_provenance() -> None:
    """A pyproject or constraints change must not stop the bootstrap before reinstalling."""
    bootstrap = (SCRIPTS / "bootstrap.ps1").read_text(encoding="utf-8")

    assert "Assert-FenestraEnvironmentInterpreter" in bootstrap
    assert "Assert-FenestraEnvironmentProvenance" not in bootstrap


def _pip_invocations(path: Path) -> list[str]:
    """Return each pip command in a script with its continuation lines joined."""
    text = path.read_text(encoding="utf-8").replace("`\n", " ")
    return [line.strip() for line in text.splitlines() if " -m pip " in line]


@pytest.mark.parametrize("script", ["bootstrap.ps1", "build-common.ps1"])
def test_pip_ignores_the_developers_configuration(script: str) -> None:
    """The developer's pip configuration and PIP_* variables cannot add an index."""
    invocations = _pip_invocations(SCRIPTS / script)

    assert invocations
    for invocation in invocations:
        assert "--isolated" in invocation, invocation


def test_bootstrap_installs_only_from_the_selected_index() -> None:
    """The install names its one index, which defaults to PyPI and must use HTTPS."""
    bootstrap = (SCRIPTS / "bootstrap.ps1").read_text(encoding="utf-8")
    installs = [line for line in _pip_invocations(SCRIPTS / "bootstrap.ps1") if "install" in line]

    assert len(installs) == 1
    assert "--index-url $PackageIndexUrl" in installs[0]
    assert "--extra-index-url" not in bootstrap
    assert '[string] $PackageIndexUrl = "https://pypi.org/simple"' in bootstrap
    assert '[ValidatePattern("^https://\\S+$")]' in bootstrap
