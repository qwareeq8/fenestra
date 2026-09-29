"""Tests for frozen Qt and WebEngine deployment verification."""

from __future__ import annotations

import shutil
import struct
from pathlib import Path
from typing import cast

from scripts.verify_qt_deployment import (
    REQUIRED_BUNDLE_FILES,
    REQUIRED_WEBENGINE_RESOURCES,
    verify_qt_bundle,
)


def _write_pe(path: Path, machine: int = 0x8664) -> None:
    image = bytearray(0x100)
    image[0:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", image, 0x84, machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image)


def _create_bundle(tmp_path: Path) -> tuple[Path, Path]:
    bundle = tmp_path / "Fenestra"
    internal = bundle / "_internal"
    source = tmp_path / "site-packages" / "PySide6"
    _write_pe(bundle / "Fenestra.exe")

    for relative_path in REQUIRED_BUNDLE_FILES:
        destination = internal / Path(relative_path)
        if destination.suffix.lower() in {".dll", ".exe", ".pyd"}:
            _write_pe(destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            text_content = (
                "[Paths]\nPrefix = .\n" if destination.name == "qt.conf" else "test data\n"
            )
            destination.write_text(text_content, encoding="utf-8")

    asset = internal / "frontend" / "dist" / "assets" / "index-test.js"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_text("export {};\n", encoding="utf-8")

    for name in REQUIRED_WEBENGINE_RESOURCES:
        source_file = source / "resources" / name
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_bytes(f"resource:{name}".encode())
        bundle_file = internal / "PySide6" / "resources" / name
        bundle_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, bundle_file)

    for relative_path, resource_content in (
        ("translations/qtwebengine_locales/en-US.pak", b"locale"),
        ("translations/qtwebengine_en.qm", b"translation"),
    ):
        source_file = source / Path(relative_path)
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_bytes(resource_content)
        bundle_file = internal / "PySide6" / Path(relative_path)
        bundle_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, bundle_file)

    for relative_path in (
        "QtWebEngineProcess.exe",
        "plugins/platforms/qwindows.dll",
    ):
        source_file = source / Path(relative_path)
        source_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(internal / "PySide6" / Path(relative_path), source_file)

    _write_pe(internal / "python312.dll")
    _write_pe(internal / "python3.dll")
    _write_pe(internal / "pywin32_system32" / "pythoncom312.dll")
    _write_pe(internal / "pywin32_system32" / "pywintypes312.dll")
    return bundle, source


def test_complete_x64_qt_bundle_passes(tmp_path: Path) -> None:
    bundle, source = _create_bundle(tmp_path)

    report = verify_qt_bundle(architecture="x64", bundle=bundle, source_pyside=source)

    assert report["status"] == "pass"
    assert report["errors"] == []
    pe_report = cast(dict[str, object], report["peArchitecture"])
    assert cast(int, pe_report["checkedFileCount"]) > 10


def test_missing_platform_plugin_fails(tmp_path: Path) -> None:
    bundle, source = _create_bundle(tmp_path)
    (bundle / "_internal" / "PySide6" / "plugins" / "platforms" / "qwindows.dll").unlink()

    report = verify_qt_bundle(architecture="x64", bundle=bundle, source_pyside=source)

    assert report["status"] == "fail"
    assert any("qwindows.dll" in error for error in cast(list[str], report["errors"]))


def test_cross_architecture_qt_binary_fails(tmp_path: Path) -> None:
    bundle, source = _create_bundle(tmp_path)
    _write_pe(bundle / "_internal" / "PySide6" / "Qt6Core.dll", machine=0xAA64)

    report = verify_qt_bundle(architecture="x64", bundle=bundle, source_pyside=source)

    assert report["status"] == "fail"
    assert any("Qt6Core.dll is arm64" in error for error in cast(list[str], report["errors"]))
