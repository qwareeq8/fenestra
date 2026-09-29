"""Tests for architecture-bearing smoke-test evidence."""

import json
import sys
from unittest.mock import MagicMock

import pytest

from fenestra.app import __main__ as app_main
from fenestra.app.config import APP_NAME, APP_VERSION


def _write_minimal_pe(path, machine: int) -> None:
    """Write the PE fields needed by the process-architecture detector."""
    contents = bytearray(0x86)
    contents[0:2] = b"MZ"
    contents[0x3C:0x40] = (0x80).to_bytes(4, "little")
    contents[0x80:0x84] = b"PE\0\0"
    contents[0x84:0x86] = machine.to_bytes(2, "little")
    path.write_bytes(contents)


@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        (0x014C, "x86"),
        (0x8664, "x64"),
        (0xAA64, "arm64"),
    ],
)
def test_get_process_architecture_reads_the_executable_pe(tmp_path, machine, expected):
    """The process architecture must come from the executable PE Machine field."""
    executable = tmp_path / "python.exe"
    _write_minimal_pe(executable, machine)

    assert app_main._get_process_architecture(str(executable)) == expected


def test_get_process_architecture_rejects_an_unknown_pe_machine(tmp_path):
    """An unknown PE Machine value must not be inferred from the host architecture."""
    executable = tmp_path / "python.exe"
    _write_minimal_pe(executable, 0xFFFF)

    assert app_main._get_process_architecture(str(executable)) == "unknown"


def test_get_process_architecture_falls_back_for_a_non_pe(monkeypatch, tmp_path):
    """Source tests on non-Windows hosts may use the normalized platform architecture."""
    executable = tmp_path / "python"
    executable.write_bytes(b"not a PE image")
    monkeypatch.setattr(app_main.platform, "machine", lambda: "aarch64")

    assert app_main._get_process_architecture(str(executable)) == "arm64"


def test_new_smoke_report_records_required_release_identity(monkeypatch, tmp_path):
    """Smoke evidence must identify the app, version, process, and frozen state."""
    executable = tmp_path / "Fenestra.exe"
    _write_minimal_pe(executable, 0xAA64)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    checks = []

    report = app_main._new_smoke_report(checks)

    assert report == {
        "schemaVersion": 1,
        "application": APP_NAME,
        "version": APP_VERSION,
        "executable": str(executable),
        "frozen": True,
        "platformMachine": app_main.platform.machine(),
        "processArchitecture": "arm64",
        "pointerBits": app_main.struct.calcsize("P") * 8,
        "checks": checks,
    }


def test_write_smoke_report_is_valid_json_and_replaces_atomically(tmp_path):
    """The windowed smoke report must be complete JSON without a leftover temporary file."""
    report_path = tmp_path / "nested" / "smoke.json"
    report = {"schemaVersion": 1, "application": APP_NAME}

    app_main._write_smoke_report(str(report_path), report)

    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert list(report_path.parent.glob("*.tmp")) == []


def test_create_smoke_settings_backend_uses_an_explicit_ini_file(tmp_path):
    """Smoke settings must explicitly use an INI file under the temporary directory."""
    settings_format = object()
    qsettings = MagicMock()
    qsettings.Format.IniFormat = settings_format
    backend = object()
    qsettings.return_value = backend
    qt_core = MagicMock(QSettings=qsettings)

    result = app_main._create_smoke_settings_backend(qt_core, str(tmp_path))

    assert result is backend
    qsettings.assert_called_once_with(str(tmp_path / "settings.ini"), settings_format)
