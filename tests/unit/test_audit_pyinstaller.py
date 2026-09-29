"""Tests for post-PyInstaller provenance and architecture auditing."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import cast

import pytest

from scripts.audit_pyinstaller import PYINSTALLER_WARNFILE_HEADER, audit_pyinstaller


def _write_pe(path: Path, machine: int = 0x8664) -> None:
    image = bytearray(0x100)
    image[0:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", image, 0x84, machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image)


def _create_build(tmp_path: Path) -> dict[str, Path]:
    build_dir = tmp_path / "build" / "Fenestra"
    bundle = tmp_path / "dist" / "Fenestra"
    python_prefix = tmp_path / "venv"
    python_base_prefix = tmp_path / "python"
    windows_root = tmp_path / "Windows"
    transcript = tmp_path / "pyinstaller.log"

    native_source = python_prefix / "Lib" / "site-packages" / "sample.pyd"
    runtime_source = python_base_prefix / "python312.dll"
    _write_pe(native_source)
    _write_pe(runtime_source)
    _write_pe(bundle / "Fenestra.exe")
    _write_pe(bundle / "_internal" / "sample.pyd")
    _write_pe(bundle / "_internal" / "python312.dll")

    build_dir.mkdir(parents=True)
    entries = [
        ("sample.pyd", str(native_source), "EXTENSION"),
        ("python312.dll", str(runtime_source), "BINARY"),
    ]
    (build_dir / "Analysis-00.toc").write_text(repr(([], entries)), encoding="utf-8")
    (build_dir / "COLLECT-00.toc").write_text(repr((entries,)), encoding="utf-8")
    (build_dir / "warn-Fenestra.txt").write_text(PYINSTALLER_WARNFILE_HEADER, encoding="utf-8")
    (build_dir / "xref-Fenestra.html").write_text("<html></html>\n", encoding="utf-8")
    transcript.write_text("PyInstaller completed successfully.\n", encoding="utf-8")
    return {
        "build_dir": build_dir,
        "bundle": bundle,
        "python_prefix": python_prefix,
        "python_base_prefix": python_base_prefix,
        "windows_root": windows_root,
        "transcript": transcript,
    }


def _audit(paths: dict[str, Path]) -> dict[str, object]:
    return audit_pyinstaller(
        architecture="x64",
        build_dir=paths["build_dir"],
        bundle=paths["bundle"],
        python_prefix=paths["python_prefix"],
        python_base_prefix=paths["python_base_prefix"],
        transcript=paths["transcript"],
        windows_roots=[paths["windows_root"]],
    )


def test_clean_analysis_and_payload_pass(tmp_path: Path) -> None:
    paths = _create_build(tmp_path)

    report = _audit(paths)

    assert report["status"] == "pass"
    assert report["errors"] == []
    payload = cast(dict[str, object], report["payloadArchitecture"])
    assert payload["checkedFileCount"] == 3
    assert report["warningClassifications"] == []


def test_reviewed_foreign_platform_import_is_classified_benign(tmp_path: Path) -> None:
    paths = _create_build(tmp_path)
    warning = (
        "missing module named pwd - imported by posixpath (conditional, optional), "
        "subprocess (delayed, optional)\n"
    )
    (paths["build_dir"] / "warn-Fenestra.txt").write_text(
        PYINSTALLER_WARNFILE_HEADER + warning, encoding="utf-8"
    )

    report = _audit(paths)

    assert report["status"] == "pass"
    records = cast(list[dict[str, object]], report["warningClassifications"])
    assert records[0]["classification"] == "benign"
    assert records[0]["category"] == "foreign-platform-optional-import"
    assert records[0]["reason"]


@pytest.mark.parametrize(
    ("warning", "category"),
    (
        (
            "missing module named posix - imported by os (conditional, optional), "
            "posixpath (optional), shutil (conditional), "
            "importlib._bootstrap_external (conditional)\n",
            "foreign-platform-optional-import",
        ),
        (
            "missing module named 'pywin.dialogs' - imported by "
            "win32com.client.selecttlb (delayed), "
            "win32com.client.makepy (delayed, conditional)\n",
            "unused-pythonwin-ui",
        ),
        # The Python 3.14 standard library moved the POSIX-only branches of
        # pathlib into the package and the new pathlib._os helper, and pulls
        # netrc and _scproxy into the modulegraph.
        (
            "missing module named pwd - imported by posixpath (delayed, conditional, "
            "optional), subprocess (delayed, conditional, optional), shutil (delayed, "
            "optional), tarfile (optional), pathlib (optional), netrc (delayed, optional)\n",
            "foreign-platform-optional-import",
        ),
        (
            "missing module named grp - imported by subprocess (delayed, conditional, "
            "optional), shutil (delayed, optional), tarfile (optional), pathlib (optional)\n",
            "foreign-platform-optional-import",
        ),
        (
            "missing module named posix - imported by os (conditional, optional), "
            "posixpath (optional), shutil (conditional), "
            "importlib._bootstrap_external (conditional), pathlib._os (optional)\n",
            "foreign-platform-optional-import",
        ),
        (
            "missing module named fcntl - imported by subprocess (optional), "
            "pathlib._os (optional), keyboard._nixcommon (delayed)\n",
            "foreign-platform-optional-import",
        ),
        (
            "missing module named _scproxy - imported by urllib.request (conditional)\n",
            "foreign-platform-optional-import",
        ),
    ),
)
def test_real_reviewed_modulegraph_branches_are_benign(
    tmp_path: Path, warning: str, category: str
) -> None:
    paths = _create_build(tmp_path)
    (paths["build_dir"] / "warn-Fenestra.txt").write_text(
        PYINSTALLER_WARNFILE_HEADER + warning, encoding="utf-8"
    )

    report = _audit(paths)

    assert report["status"] == "pass"
    records = cast(list[dict[str, object]], report["warningClassifications"])
    assert records[0]["classification"] == "benign"
    assert records[0]["category"] == category


def test_unreviewed_optional_import_is_fatal(tmp_path: Path) -> None:
    paths = _create_build(tmp_path)
    warning = "missing module named critical_plugin - imported by fenestra.app (optional)\n"
    (paths["build_dir"] / "warn-Fenestra.txt").write_text(
        PYINSTALLER_WARNFILE_HEADER + warning, encoding="utf-8"
    )

    report = _audit(paths)

    assert report["status"] == "fail"
    records = cast(list[dict[str, object]], report["warningClassifications"])
    assert records[0]["classification"] == "fatal"
    assert records[0]["category"] == "unreviewed-optional-import"


@pytest.mark.parametrize(
    ("library", "classification", "category"),
    (
        ("api-ms-win-core-path-l1-1-0.dll", "benign", "windows-api-set-resolution"),
        ("kernel32.dll", "benign", "windows-system-resolution"),
        ("Qt6Core.dll", "fatal", "missing-qt-runtime"),
        ("python313.dll", "fatal", "missing-python-runtime"),
        ("mfc140u.dll", "fatal", "missing-mfc-or-vc-runtime"),
        ("libssl-3-x64.dll", "fatal", "missing-openssl-runtime"),
        ("custom-runtime.dll", "fatal", "missing-non-system-runtime"),
    ),
)
def test_library_warnings_are_classified_strictly(
    tmp_path: Path, library: str, classification: str, category: str
) -> None:
    paths = _create_build(tmp_path)
    paths["transcript"].write_text(
        f"123 WARNING: Library not found: could not resolve '{library}', "
        "dependency of 'sample.pyd'.\n",
        encoding="utf-8",
    )

    report = _audit(paths)

    records = cast(list[dict[str, object]], report["warningClassifications"])
    assert records[0]["classification"] == classification
    assert records[0]["category"] == category
    assert records[0]["reason"]
    assert report["status"] == ("pass" if classification == "benign" else "fail")


def test_assetdownloader_warning_requires_adjacent_optional_declaration(
    tmp_path: Path,
) -> None:
    paths = _create_build(tmp_path)
    plugin = (
        paths["python_prefix"]
        / "Lib/site-packages/PySide6/qml/Qt/labs/assetdownloader"
        / "qmlassetdownloaderprivateplugin.dll"
    )
    plugin.parent.mkdir(parents=True)
    (plugin.parent / "qmldir").write_text(
        "module Qt.labs.assetdownloader\noptional plugin qmlassetdownloaderprivateplugin\n",
        encoding="utf-8",
    )
    paths["transcript"].write_text(
        f"456 WARNING: QtLibraryInfo(PySide6): QML plugin binary '{plugin}' does not exist!\n",
        encoding="utf-8",
    )

    report = _audit(paths)

    assert report["status"] == "pass"
    records = cast(list[dict[str, object]], report["warningClassifications"])
    assert records[0]["category"] == "optional-pyside6-qml-plugin"
    assert records[0]["optionalDeclarationVerified"] is True


def test_missing_analysis_artifact_fails(tmp_path: Path) -> None:
    paths = _create_build(tmp_path)
    (paths["build_dir"] / "xref-Fenestra.html").unlink()

    report = _audit(paths)

    assert report["status"] == "fail"
    assert any(
        "analysis artifacts are missing" in error for error in cast(list[str], report["errors"])
    )


@pytest.mark.parametrize("encoding", ("utf-8", "utf-16"))
def test_fatal_qt_hook_transcript_fails(tmp_path: Path, encoding: str) -> None:
    paths = _create_build(tmp_path)
    paths["transcript"].write_text(
        "QtLibraryInfo(PySide6): failed to obtain Qt library info.\n", encoding=encoding
    )

    report = _audit(paths)

    assert report["status"] == "fail"
    assert any("fatal PySide6 Qt hook" in error for error in cast(list[str], report["errors"]))


def test_external_native_origin_fails(tmp_path: Path) -> None:
    paths = _create_build(tmp_path)
    foreign = tmp_path / "tools" / "qemu" / "libssl-3-x64.dll"
    _write_pe(foreign)
    entries = [("libssl-3-x64.dll", str(foreign), "BINARY")]
    (paths["build_dir"] / "Analysis-00.toc").write_text(repr(entries), encoding="utf-8")

    report = _audit(paths)

    assert report["status"] == "fail"
    errors = cast(list[str], report["errors"])
    assert any("outside the selected Python" in error for error in errors)
    assert any("qemu" in error for error in errors)


def test_pythonwin_or_mfc_remnants_fail(tmp_path: Path) -> None:
    paths = _create_build(tmp_path)
    win32ui = paths["python_prefix"] / "Lib" / "site-packages" / "pythonwin" / "win32ui.pyd"
    _write_pe(win32ui)
    entries = [("pythonwin/win32ui.pyd", str(win32ui), "EXTENSION")]
    (paths["build_dir"] / "COLLECT-00.toc").write_text(repr(entries), encoding="utf-8")
    _write_pe(paths["bundle"] / "_internal" / "mfc140u.dll")

    report = _audit(paths)

    assert report["status"] == "fail"
    assert any(
        "Pythonwin, win32ui, or MFC remnants" in error
        for error in cast(list[str], report["errors"])
    )
