"""Tests for the dependency-free PE architecture verifier."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import cast

import pytest

from scripts.pe_arch import PEFormatError, main, read_pe, verify_pe_paths


def _write_pe(path: Path, machine: int) -> None:
    image = bytearray(0x100)
    image[0:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", image, 0x84, machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image)


@pytest.mark.parametrize(
    ("machine", "architecture"),
    [(0x014C, "x86"), (0x8664, "x64"), (0xAA64, "arm64")],
)
def test_read_pe_recognizes_supported_machines(
    tmp_path: Path, machine: int, architecture: str
) -> None:
    image = tmp_path / "sample.dll"
    _write_pe(image, machine)

    result = read_pe(image)

    assert result.machine == machine
    assert result.machine_hex == f"0x{machine:04X}"
    assert result.architecture == architecture


def test_read_pe_rejects_an_invalid_header(tmp_path: Path) -> None:
    image = tmp_path / "not-a-pe.dll"
    image.write_bytes(b"not a PE file")

    with pytest.raises(PEFormatError, match="valid DOS header"):
        read_pe(image)


def test_recursive_verification_reports_cross_architecture_files(tmp_path: Path) -> None:
    _write_pe(tmp_path / "Fenestra.exe", 0x8664)
    _write_pe(tmp_path / "_internal" / "good.pyd", 0x8664)
    _write_pe(tmp_path / "_internal" / "native.node", 0x8664)
    _write_pe(tmp_path / "_internal" / "control.ocx", 0xAA64)
    _write_pe(tmp_path / "_internal" / "wrong.dll", 0xAA64)
    (tmp_path / "_internal" / "resource.pak").write_bytes(b"data")

    report = verify_pe_paths([tmp_path], expected="x64", recursive=True)

    assert report["status"] == "fail"
    assert report["checkedFileCount"] == 5
    assert any("wrong.dll is arm64" in error for error in cast(list[str], report["errors"]))
    assert any("control.ocx is arm64" in error for error in cast(list[str], report["errors"]))


def test_cli_writes_a_machine_readable_report(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    image = tmp_path / "Fenestra.exe"
    report_path = tmp_path / "pe-report.json"
    _write_pe(image, 0x8664)

    exit_code = main(
        ["--expected", "x64", "--recursive", "--json", str(report_path), str(tmp_path)]
    )

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert report["files"][0]["machine_hex"] == "0x8664"
    assert '"status": "pass"' in capsys.readouterr().out


def test_cli_inventory_accepts_a_recognized_x86_installer(tmp_path: Path) -> None:
    image = tmp_path / "FenestraSetup.exe"
    report_path = tmp_path / "installer-pe.json"
    _write_pe(image, 0x014C)

    exit_code = main(["--json", str(report_path), str(image)])

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "inventory"
    assert report["expectedArchitecture"] is None
    assert report["files"][0]["architecture"] == "x86"


def test_cli_requires_architecture_for_recursive_scans(tmp_path: Path) -> None:
    _write_pe(tmp_path / "Fenestra.exe", 0x8664)

    with pytest.raises(SystemExit, match="2"):
        main(["--recursive", str(tmp_path)])
