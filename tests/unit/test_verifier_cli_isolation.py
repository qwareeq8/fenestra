"""Tests that verifier entry points work with Python isolated mode enabled."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "script_name",
    (
        "audit_pyinstaller.py",
        "pe_arch.py",
        "verify_python_environment.py",
        "verify_qt_deployment.py",
    ),
)
def test_verifier_help_works_in_isolated_mode(script_name: str) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    script = repository_root / "scripts" / script_name

    process = subprocess.run(
        [sys.executable, "-I", str(script), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert process.returncode == 0, process.stderr
    assert "usage:" in process.stdout
