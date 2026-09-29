"""Verify a frozen Fenestra bundle's Qt, WebEngine, frontend, and PE architecture."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from scripts.pe_arch import normalize_architecture, verify_pe_paths
elif __package__:
    from .pe_arch import normalize_architecture, verify_pe_paths
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pe_arch import normalize_architecture, verify_pe_paths


REQUIRED_BUNDLE_FILES = (
    "PySide6/QtCore.pyd",
    "PySide6/QtGui.pyd",
    "PySide6/QtNetwork.pyd",
    "PySide6/QtWebChannel.pyd",
    "PySide6/QtWebEngineCore.pyd",
    "PySide6/QtWebEngineWidgets.pyd",
    "PySide6/QtWidgets.pyd",
    "PySide6/Qt6Core.dll",
    "PySide6/Qt6Gui.dll",
    "PySide6/Qt6Network.dll",
    "PySide6/Qt6WebChannel.dll",
    "PySide6/Qt6WebEngineCore.dll",
    "PySide6/Qt6WebEngineWidgets.dll",
    "PySide6/Qt6Widgets.dll",
    "PySide6/QtWebEngineProcess.exe",
    "PySide6/plugins/platforms/qwindows.dll",
    "PySide6/pyside6.abi3.dll",
    "PySide6/qt.conf",
    "shiboken6/Shiboken.pyd",
    "shiboken6/shiboken6.abi3.dll",
    "frontend/dist/index.html",
)
REQUIRED_WEBENGINE_RESOURCES = (
    "icudtl.dat",
    "qtwebengine_devtools_resources.pak",
    "qtwebengine_resources.pak",
    "qtwebengine_resources_100p.pak",
    "qtwebengine_resources_200p.pak",
    "v8_context_snapshot.bin",
)
PROHIBITED_UNUSED_NAMES = frozenset({"mfc140u.dll", "win32ui.pyd"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _find_pyside_root() -> Path | None:
    spec = importlib.util.find_spec("PySide6")
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).resolve().parent


def _relative_files(root: Path, *, pattern: str = "*") -> dict[str, Path]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): path
        for path in sorted(root.rglob(pattern), key=lambda item: str(item).casefold())
        if path.is_file()
    }


def _compare_source_files(
    *,
    source_root: Path,
    bundle_root: Path,
    relative_directory: str,
    pattern: str,
) -> tuple[dict[str, object], list[str]]:
    source_directory = source_root / Path(relative_directory)
    bundle_directory = bundle_root / Path(relative_directory)
    source_files = _relative_files(source_directory, pattern=pattern)
    bundle_files = _relative_files(bundle_directory, pattern=pattern)
    errors: list[str] = []

    if not source_files:
        errors.append(
            f"The selected PySide6 installation has no {pattern} files under {source_directory}."
        )

    missing = sorted(set(source_files) - set(bundle_files))
    unexpected = sorted(set(bundle_files) - set(source_files))
    if missing:
        errors.append(f"The bundle is missing {relative_directory} files: {', '.join(missing)}.")
    if unexpected:
        errors.append(
            f"The bundle has unexpected {relative_directory} files: {', '.join(unexpected)}."
        )

    mismatched = [
        relative_path
        for relative_path in sorted(set(source_files) & set(bundle_files))
        if _sha256(source_files[relative_path]) != _sha256(bundle_files[relative_path])
    ]
    if mismatched:
        errors.append(
            f"The bundle has modified {relative_directory} files: {', '.join(mismatched)}."
        )

    return (
        {
            "relativeDirectory": relative_directory,
            "pattern": pattern,
            "sourceCount": len(source_files),
            "bundleCount": len(bundle_files),
            "missing": missing,
            "unexpected": unexpected,
            "hashMismatches": mismatched,
        },
        errors,
    )


def verify_qt_bundle(
    *,
    architecture: str,
    bundle: Path,
    source_pyside: Path | None = None,
) -> dict[str, object]:
    """Verify required frozen files, source parity, and all loadable PE images."""
    expected = normalize_architecture(architecture)
    bundle = bundle.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, object]] = []

    if not bundle.is_dir():
        errors.append(f"The frozen bundle directory does not exist: {bundle}.")
        return {
            "schemaVersion": 1,
            "requestedArchitecture": expected,
            "bundle": str(bundle),
            "status": "fail",
            "checks": checks,
            "errors": errors,
            "warnings": warnings,
        }

    executable = bundle / "Fenestra.exe"
    internal = bundle / "_internal"
    if not executable.is_file():
        errors.append(f"The frozen executable is missing: {executable}.")
    if not internal.is_dir():
        errors.append(f"The PyInstaller _internal directory is missing: {internal}.")

    missing_required = [
        relative_path
        for relative_path in REQUIRED_BUNDLE_FILES
        if not (internal / Path(relative_path)).is_file()
    ]
    if missing_required:
        errors.append("Required bundle files are missing: " + ", ".join(missing_required) + ".")
    checks.append(
        {
            "name": "Required Qt, WebEngine, Shiboken, and frontend files",
            "requiredCount": len(REQUIRED_BUNDLE_FILES),
            "missing": missing_required,
        }
    )

    qt_conf = internal / "PySide6" / "qt.conf"
    qt_conf_text = qt_conf.read_text(encoding="utf-8-sig") if qt_conf.is_file() else ""
    normalized_qt_conf = {
        line.strip().casefold() for line in qt_conf_text.splitlines() if line.strip()
    }
    qt_conf_valid = {"[paths]", "prefix = ."}.issubset(normalized_qt_conf)
    if qt_conf.is_file() and not qt_conf_valid:
        errors.append("PySide6/qt.conf does not set the frozen Qt prefix to the bundle directory.")
    checks.append(
        {
            "name": "Frozen Qt path configuration",
            "valid": qt_conf_valid,
        }
    )

    frontend_assets = _relative_files(internal / "frontend" / "dist" / "assets")
    if not frontend_assets:
        errors.append("The frozen frontend assets directory is empty or missing.")
    checks.append(
        {
            "name": "Frontend assets",
            "assetCount": len(frontend_assets),
            "files": sorted(frontend_assets),
        }
    )

    resource_root = internal / "PySide6" / "resources"
    missing_webengine_resources = [
        name for name in REQUIRED_WEBENGINE_RESOURCES if not (resource_root / name).is_file()
    ]
    if missing_webengine_resources:
        errors.append(
            "Required Qt WebEngine resources are missing: "
            + ", ".join(missing_webengine_resources)
            + "."
        )
    checks.append(
        {
            "name": "Required Qt WebEngine resources",
            "missing": missing_webengine_resources,
        }
    )

    locale_root = internal / "PySide6" / "translations" / "qtwebengine_locales"
    locales = sorted(path.name for path in locale_root.glob("*.pak") if path.is_file())
    if "en-US.pak" not in locales:
        errors.append("Qt WebEngine locale data is missing en-US.pak.")
    checks.append(
        {
            "name": "Qt WebEngine locales",
            "localeCount": len(locales),
            "hasEnglishUnitedStates": "en-US.pak" in locales,
        }
    )

    prohibited = sorted(
        path.relative_to(internal).as_posix()
        for path in internal.rglob("*")
        if path.is_file()
        and (
            path.name.casefold() in PROHIBITED_UNUSED_NAMES
            or "pythonwin" in {part.casefold() for part in path.relative_to(internal).parts}
        )
    )
    if prohibited:
        errors.append("Unused Pythonwin/MFC payload files remain: " + ", ".join(prohibited) + ".")
    checks.append({"name": "Unused Pythonwin/MFC exclusion", "found": prohibited})

    selected_source = source_pyside.resolve() if source_pyside is not None else _find_pyside_root()
    if selected_source is None or not selected_source.is_dir():
        errors.append(
            "The active PySide6 package root could not be located for deployment comparison."
        )
    else:
        checks.append({"name": "PySide6 source root", "path": str(selected_source)})
        source_pairs = (
            ("resources", "*"),
            ("translations/qtwebengine_locales", "*.pak"),
            ("translations", "qtwebengine_*.qm"),
        )
        for relative_directory, pattern in source_pairs:
            comparison, comparison_errors = _compare_source_files(
                source_root=selected_source,
                bundle_root=internal / "PySide6",
                relative_directory=relative_directory,
                pattern=pattern,
            )
            checks.append(comparison)
            errors.extend(comparison_errors)

        for relative_path in (
            "QtWebEngineProcess.exe",
            "plugins/platforms/qwindows.dll",
        ):
            source_file = selected_source / Path(relative_path)
            bundle_file = internal / "PySide6" / Path(relative_path)
            if not source_file.is_file():
                errors.append(f"The selected PySide6 installation is missing {source_file}.")
            elif bundle_file.is_file() and _sha256(source_file) != _sha256(bundle_file):
                errors.append(f"The bundled PySide6 file differs from its source: {relative_path}.")

    interpreter_dlls = []
    for path in internal.glob("python*.dll"):
        version_suffix = path.stem.removeprefix("python")
        if path.is_file() and len(version_suffix) >= 2 and version_suffix.isdigit():
            interpreter_dlls.append(path.name)
    interpreter_dlls.sort()
    if len(interpreter_dlls) != 1:
        errors.append(
            "Expected exactly one versioned Python runtime DLL in _internal; found: "
            + (", ".join(interpreter_dlls) if interpreter_dlls else "none")
            + "."
        )
    checks.append({"name": "Python runtime DLL", "files": interpreter_dlls})

    for family, pattern in (
        ("pythoncom", "pywin32_system32/pythoncom*.dll"),
        ("pywintypes", "pywin32_system32/pywintypes*.dll"),
    ):
        matches = sorted(path.relative_to(internal).as_posix() for path in internal.glob(pattern))
        if len(matches) != 1:
            errors.append(
                f"Expected exactly one {family} runtime DLL; found: "
                + (", ".join(matches) if matches else "none")
                + "."
            )
        checks.append({"name": f"{family} runtime DLL", "files": matches})

    pe_report = verify_pe_paths([bundle], expected=expected, recursive=True)
    checks.append(
        {
            "name": "Recursive PE architecture scan",
            "checkedFileCount": pe_report["checkedFileCount"],
            "status": pe_report["status"],
        }
    )
    errors.extend(cast(list[str], pe_report["errors"]))

    return {
        "schemaVersion": 1,
        "requestedArchitecture": expected,
        "bundle": str(bundle),
        "sourcePySide6": str(selected_source) if selected_source is not None else None,
        "status": "pass" if not errors else "fail",
        "checks": checks,
        "peArchitecture": pe_report,
        "errors": errors,
        "warnings": warnings,
    }


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", required=True, choices=("x64",))
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the frozen Qt deployment verifier."""
    args = build_parser().parse_args(argv)
    report = verify_qt_bundle(architecture=args.architecture, bundle=args.bundle)
    _write_report(args.report, report)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
