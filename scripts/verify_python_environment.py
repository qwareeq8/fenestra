"""Verify that the active Python environment can produce the requested Windows payload."""

from __future__ import annotations

import argparse
import ctypes
import importlib.metadata
import importlib.util
import json
import os
import platform
import struct
import subprocess
import sys
import sysconfig
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from scripts.pe_arch import (
        MACHINE_ARCHITECTURES,
        normalize_architecture,
        read_pe,
        verify_pe_paths,
    )
elif __package__:
    from .pe_arch import MACHINE_ARCHITECTURES, normalize_architecture, read_pe, verify_pe_paths
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pe_arch import MACHINE_ARCHITECTURES, normalize_architecture, read_pe, verify_pe_paths


REQUIRED_BINARY_DISTRIBUTIONS = (
    "PySide6",
    "PySide6_Essentials",
    "PySide6_Addons",
    "shiboken6",
    "pywin32",
    "pyinstaller",
)
OPTIONAL_BINARY_DISTRIBUTIONS = ("ruff",)
CRITICAL_PATH_PATTERNS = (
    "Qt6*.dll",
    "icu*.dll",
    "libcrypto*.dll",
    "libssl*.dll",
    "msvcp*.dll",
    "python3*.dll",
    "vcruntime*.dll",
)
CONDA_PATH_MARKERS = ("anaconda", "conda", "mambaforge", "miniforge", "miniconda")
MINIMUM_PYTHON_VERSION = (3, 12)


QT_IMPORT_SCRIPT = r"""
import json
from pathlib import Path

import PySide6
import shiboken6
from PySide6 import (
    QtCore,
    QtGui,
    QtNetwork,
    QtWebChannel,
    QtWebEngineCore,
    QtWebEngineWidgets,
    QtWidgets,
)

root = Path(PySide6.__file__).resolve().parent
shiboken_root = Path(shiboken6.__file__).resolve().parent
ssl = QtNetwork.QSslSocket
print(json.dumps({
    "qtVersion": QtCore.qVersion(),
    "packageRoot": str(root),
    "shibokenRoot": str(shiboken_root),
    "modules": {
        module.__name__: str(Path(module.__file__).resolve())
        for module in (
            QtCore,
            QtGui,
            QtNetwork,
            QtWebChannel,
            QtWebEngineCore,
            QtWebEngineWidgets,
            QtWidgets,
        )
    },
    "binaries": {
        "Qt6Core": str(root / "Qt6Core.dll"),
        "Qt6Gui": str(root / "Qt6Gui.dll"),
        "Qt6Network": str(root / "Qt6Network.dll"),
        "Qt6WebChannel": str(root / "Qt6WebChannel.dll"),
        "Qt6WebEngineCore": str(root / "Qt6WebEngineCore.dll"),
        "Qt6WebEngineWidgets": str(root / "Qt6WebEngineWidgets.dll"),
        "Qt6Widgets": str(root / "Qt6Widgets.dll"),
        "QtWebEngineProcess": str(root / "QtWebEngineProcess.exe"),
        "qwindows": str(root / "plugins" / "platforms" / "qwindows.dll"),
        "pysideAbi": str(root / "pyside6.abi3.dll"),
        "shibokenExtension": str(shiboken_root / "Shiboken.pyd"),
        "shibokenAbi": str(shiboken_root / "shiboken6.abi3.dll"),
    },
    "ssl": {
        "supported": ssl.supportsSsl(),
        "availableBackends": ssl.availableBackends(),
        "activeBackend": ssl.activeBackend(),
        "buildVersion": ssl.sslLibraryBuildVersionString(),
        "runtimeVersion": ssl.sslLibraryVersionString(),
    },
}))
"""

WIN32_IMPORT_SCRIPT = r"""
import json
from pathlib import Path

import comtypes
import pythoncom
import pywintypes
import win32api
import win32event
import win32gui

modules = (win32api, win32gui, win32event, pythoncom, pywintypes, comtypes)
print(json.dumps({module.__name__: str(Path(module.__file__).resolve()) for module in modules}))
"""

QT_HOOK_SCRIPT = r"""
import json
from PyInstaller.utils.hooks.qt import pyside6_library_info

if pyside6_library_info.version is None:
    raise RuntimeError("PyInstaller could not obtain PySide6 Qt library information.")
print(json.dumps({
    "version": pyside6_library_info.version,
    "location": pyside6_library_info.location,
}))
"""


def _path_has_conda_marker(path: str | Path) -> bool:
    components = str(path).replace("\\", "/").casefold().split("/")
    return any(
        component.startswith(marker) for component in components for marker in CONDA_PATH_MARKERS
    )


def conda_status(
    *,
    prefix: str | Path,
    base_prefix: str | Path,
    version: str,
    environ: Mapping[str, str],
) -> dict[str, object]:
    """Determine whether the active interpreter, rather than ambient tooling, is Conda-based."""
    prefix_path = Path(prefix).resolve()
    base_prefix_path = Path(base_prefix).resolve()
    evidence: list[str] = []
    ambient: dict[str, str] = {}

    for candidate in (prefix_path, base_prefix_path):
        if _path_has_conda_marker(candidate):
            evidence.append(f"Interpreter prefix contains a Conda-family marker: {candidate}.")
        if (candidate / "conda-meta").is_dir():
            evidence.append(f"Interpreter prefix contains conda-meta: {candidate}.")
    if "conda" in version.casefold():
        evidence.append("sys.version identifies a Conda build.")

    for name in ("CONDA_DEFAULT_ENV", "CONDA_EXE", "CONDA_PREFIX", "CONDA_PYTHON_EXE"):
        value = environ.get(name)
        if value:
            ambient[name] = value

    active_conda_prefix = environ.get("CONDA_PREFIX")
    if active_conda_prefix:
        resolved_active = Path(active_conda_prefix).resolve()
        if resolved_active in (prefix_path, base_prefix_path):
            evidence.append(f"CONDA_PREFIX identifies the active interpreter: {resolved_active}.")

    return {
        "active": bool(evidence),
        "evidence": evidence,
        "ambientVariables": ambient,
    }


def _machine_name(value: str) -> str:
    try:
        return normalize_architecture(value)
    except ValueError:
        return "unknown"


def python_version_is_supported(version: Sequence[int]) -> bool:
    """Return whether a Python version satisfies Fenestra's release-build minimum."""
    return tuple(version[:2]) >= MINIMUM_PYTHON_VERSION


def _windows_architecture() -> dict[str, object]:
    """Return native OS and process architectures, using IsWow64Process2 when available."""
    fallback_process = _machine_name(platform.machine())
    result: dict[str, object] = {
        "nativeArchitecture": fallback_process,
        "processArchitecture": fallback_process,
        "processMachine": None,
        "nativeMachine": None,
        "source": "platform.machine",
    }
    if sys.platform != "win32":
        return result

    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.argtypes = []
        get_current_process.restype = wintypes.HANDLE
        is_wow64_process2 = kernel32.IsWow64Process2
        is_wow64_process2.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.USHORT),
            ctypes.POINTER(wintypes.USHORT),
        ]
        is_wow64_process2.restype = wintypes.BOOL

        process_machine = wintypes.USHORT()
        native_machine = wintypes.USHORT()
        if not is_wow64_process2(
            get_current_process(), ctypes.byref(process_machine), ctypes.byref(native_machine)
        ):
            raise OSError(ctypes.get_last_error(), "IsWow64Process2 failed.")

        native_architecture = MACHINE_ARCHITECTURES.get(native_machine.value, "unknown")
        result = {
            "nativeArchitecture": native_architecture,
            "processArchitecture": MACHINE_ARCHITECTURES.get(process_machine.value, "unknown"),
            "processMachine": f"0x{process_machine.value:04X}",
            "nativeMachine": f"0x{native_machine.value:04X}",
            "source": "IsWow64Process2",
        }
    except (AttributeError, OSError, ValueError):
        native_environment = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get(
            "PROCESSOR_ARCHITECTURE", ""
        )
        result["nativeArchitecture"] = _machine_name(native_environment)
        result["source"] = "environment fallback"
    return result


def reconcile_process_architecture(
    executable_architecture: str | None,
    os_architecture: Mapping[str, object],
) -> dict[str, object]:
    """Reconcile the process architecture using the active Python executable's PE header."""
    resolved_architecture = executable_architecture or "unknown"
    os_process_architecture = str(os_architecture.get("processArchitecture", "unknown"))
    native_architecture = str(os_architecture.get("nativeArchitecture", "unknown"))

    accepted_x64_on_arm64_ambiguity = (
        resolved_architecture == "x64"
        and os_process_architecture == "unknown"
        and native_architecture == "arm64"
        and os_architecture.get("source") == "IsWow64Process2"
        and str(os_architecture.get("processMachine", "")).upper() == "0X0000"
        and str(os_architecture.get("nativeMachine", "")).upper() == "0XAA64"
    )
    native_process_with_unknown_machine = (
        resolved_architecture != "unknown"
        and native_architecture != "unknown"
        and resolved_architecture == native_architecture
        and os_process_architecture == "unknown"
        and os_architecture.get("source") == "IsWow64Process2"
        and str(os_architecture.get("processMachine", "")).upper() == "0X0000"
    )
    evidence_consistent = (
        (resolved_architecture != "unknown" and resolved_architecture == os_process_architecture)
        or native_process_with_unknown_machine
        or accepted_x64_on_arm64_ambiguity
    )
    is_emulated = evidence_consistent and (
        resolved_architecture not in {"unknown", native_architecture}
        and native_architecture != "unknown"
    )

    return {
        "processArchitecture": resolved_architecture,
        "osReportedProcessArchitecture": os_process_architecture,
        "processArchitectureSource": "pythonExecutablePe",
        "architectureEvidenceConsistent": evidence_consistent,
        "nativeProcessWithUnknownMachine": native_process_with_unknown_machine,
        "acceptedX64OnArm64Ambiguity": accepted_x64_on_arm64_ambiguity,
        "isEmulated": is_emulated,
    }


def _is_elevated() -> bool | None:
    if sys.platform != "win32":
        return None
    try:
        from ctypes import wintypes

        function = ctypes.WinDLL("shell32", use_last_error=True).IsUserAnAdmin
        function.argtypes = []
        function.restype = wintypes.BOOL
        return bool(function())
    except (AttributeError, OSError):
        return None


def parse_wheel_tags(wheel_text: str) -> list[str]:
    """Extract all platform tags from an installed WHEEL metadata file."""
    return [
        line.partition(":")[2].strip()
        for line in wheel_text.splitlines()
        if line.casefold().startswith("tag:")
    ]


def wheel_tags_match_architecture(tags: Sequence[str], architecture: str) -> bool:
    """Return whether wheel tags represent the requested Windows architecture."""
    expected = "win_amd64" if normalize_architecture(architecture) == "x64" else "win_arm64"
    return bool(tags) and all(tag.rsplit("-", 1)[-1] == expected for tag in tags)


def _distribution_report(name: str, architecture: str) -> dict[str, object]:
    distribution = importlib.metadata.distribution(name)
    wheel_text = distribution.read_text("WHEEL") or ""
    tags = parse_wheel_tags(wheel_text)
    return {
        "name": distribution.metadata.get("Name", name),
        "version": distribution.version,
        "tags": tags,
        "matchesArchitecture": wheel_tags_match_architecture(tags, architecture),
        "location": str(Path(str(distribution.locate_file(""))).resolve()),
    }


_CHILD_TIMEOUT_SECONDS = 60


def _run_json_child(name: str, source: str) -> tuple[dict[str, object] | None, dict[str, object]]:
    command = [sys.executable, "-I", "-c", source]
    try:
        process = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=_CHILD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        return None, {
            "name": name,
            "command": [sys.executable, "-I", "-c", "<isolated script>"],
            "returnCode": None,
            "stderr": str(error),
            "timedOut": True,
            "timeoutSeconds": _CHILD_TIMEOUT_SECONDS,
        }
    details: dict[str, object] = {
        "name": name,
        "command": [sys.executable, "-I", "-c", "<isolated script>"],
        "returnCode": process.returncode,
        "stderr": process.stderr.strip(),
    }
    if process.returncode != 0:
        details["stdout"] = process.stdout.strip()
        return None, details
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        details["stdout"] = process.stdout.strip()
        details["parseError"] = str(exc)
        return None, details
    details["result"] = payload
    return payload, details


def find_python_dlls(prefixes: Sequence[str | Path], *, major: int, minor: int) -> list[Path]:
    """Find candidate Python shared libraries below selected interpreter prefixes."""
    name = f"python{major}{minor}.dll"
    candidates: dict[str, Path] = {}
    for raw_prefix in prefixes:
        prefix = Path(raw_prefix)
        for candidate in (prefix / name, prefix / "DLLs" / name):
            if candidate.is_file():
                candidates[str(candidate.resolve()).casefold()] = candidate.resolve()

    configured = sysconfig.get_config_var("LDLIBRARY")
    if configured:
        for raw_prefix in prefixes:
            candidate = Path(raw_prefix) / configured
            if candidate.is_file():
                candidates[str(candidate.resolve()).casefold()] = candidate.resolve()
    return sorted(candidates.values(), key=lambda path: str(path).casefold())


def _is_below(path: Path, roots: Sequence[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved == root or root in resolved.parents for root in roots)


def find_external_critical_dlls(
    path_value: str,
    *,
    allowed_roots: Sequence[str | Path],
) -> list[Path]:
    """Find native runtime candidates on PATH outside reviewed interpreter and OS roots."""
    roots = [Path(root).resolve() for root in allowed_roots]
    found: dict[str, Path] = {}
    for raw_directory in path_value.split(os.pathsep):
        if not raw_directory:
            continue
        directory = Path(raw_directory)
        if not directory.is_dir() or _is_below(directory, roots):
            continue
        for pattern in CRITICAL_PATH_PATTERNS:
            for candidate in directory.glob(pattern):
                if candidate.is_file():
                    found[str(candidate.resolve()).casefold()] = candidate.resolve()
    return sorted(found.values(), key=lambda path: str(path).casefold())


def _critical_binary_report(
    paths: Sequence[str | Path], architecture: str, allowed_roots: Sequence[Path]
) -> tuple[list[dict[str, object]], list[str]]:
    entries: list[dict[str, object]] = []
    errors: list[str] = []
    selected: dict[str, Path] = {}
    for raw_path in paths:
        path = Path(raw_path)
        key = str(path.resolve()).casefold() if path.exists() else str(path).casefold()
        selected[key] = path

    for path in sorted(selected.values(), key=lambda item: str(item).casefold()):
        if not path.is_file():
            errors.append(f"Required native binary is missing: {path}.")
            continue
        try:
            pe = read_pe(path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        provenance_ok = _is_below(path, allowed_roots)
        matches = pe.architecture == architecture
        entries.append(
            {
                "path": pe.path,
                "architecture": pe.architecture,
                "machine": pe.machine_hex,
                "matchesArchitecture": matches,
                "reviewedProvenance": provenance_ok,
            }
        )
        if not matches:
            errors.append(
                f"{pe.path} is {pe.architecture} ({pe.machine_hex}); expected {architecture}."
            )
        if not provenance_ok:
            errors.append(
                f"Native binary resolved outside the selected Python environment: {pe.path}."
            )
    return entries, errors


def verify_environment(
    *,
    architecture: str,
    mode: str,
    payload: Path | None = None,
) -> dict[str, object]:
    """Verify the active interpreter and, in full mode, its release dependencies."""
    expected = normalize_architecture(architecture)
    errors: list[str] = []
    warnings: list[str] = []

    python_executable = Path(sys.executable).resolve()
    prefix = Path(sys.prefix).resolve()
    base_prefix = Path(sys.base_prefix).resolve()
    os_architecture = _windows_architecture()
    pointer_bits = struct.calcsize("P") * 8
    python_pe: dict[str, object] | None = None
    python_architecture: str | None = None

    if sys.platform != "win32":
        errors.append(f"Windows is required for a Windows release build; found {sys.platform}.")
    else:
        try:
            executable_pe = read_pe(python_executable)
            python_pe = {
                "machine": executable_pe.machine_hex,
                "architecture": executable_pe.architecture,
            }
            python_architecture = executable_pe.architecture
        except ValueError as exc:
            errors.append(str(exc))

    process_report = reconcile_process_architecture(python_architecture, os_architecture)
    process_architecture = process_report["processArchitecture"]
    if sys.platform == "win32" and process_architecture != expected:
        errors.append(
            f"The active Python executable is {process_architecture}; expected {expected}."
        )
    if sys.platform == "win32" and not process_report["architectureEvidenceConsistent"]:
        errors.append(
            "The active Python executable PE architecture "
            f"({process_architecture}) conflicts with the OS-reported process architecture "
            f"({process_report['osReportedProcessArchitecture']})."
        )

    if pointer_bits != 64:
        errors.append(f"A 64-bit Python process is required; found {pointer_bits}-bit Python.")
    if platform.python_implementation() != "CPython":
        errors.append(f"Release builds require CPython; found {platform.python_implementation()}.")
    version_tuple = tuple(sys.version_info[:3])
    if not python_version_is_supported(version_tuple):
        minimum = ".".join(str(component) for component in MINIMUM_PYTHON_VERSION)
        actual = ".".join(str(component) for component in version_tuple)
        errors.append(f"Release builds require Python {minimum} or newer; found {actual}.")

    conda = conda_status(
        prefix=prefix,
        base_prefix=base_prefix,
        version=sys.version,
        environ=os.environ,
    )
    if conda["active"]:
        errors.append(
            "The active interpreter is Conda-based. Select an official CPython interpreter."
        )
    elif conda["ambientVariables"]:
        warnings.append(
            "Ambient Conda variables were detected and must be removed from build children."
        )

    if os.environ.get("PYTHONHOME"):
        errors.append("PYTHONHOME is set and can redirect the selected interpreter.")
    if os.environ.get("PYTHONPATH"):
        errors.append("PYTHONPATH is set and can contaminate release imports.")

    path_conda_entries = [
        entry
        for entry in os.environ.get("PATH", "").split(os.pathsep)
        if entry and _path_has_conda_marker(entry)
    ]
    if path_conda_entries:
        errors.append(
            "PATH contains Conda-family directories: " + ", ".join(path_conda_entries) + "."
        )

    elevated = _is_elevated()
    if elevated:
        warnings.append(
            "The current process is elevated. Run PyInstaller from a non-administrator terminal."
        )

    report: dict[str, object] = {
        "schemaVersion": 1,
        "requestedArchitecture": expected,
        "mode": mode,
        "python": {
            "executable": str(python_executable),
            "version": sys.version,
            "versionTuple": list(version_tuple),
            "minimumVersion": list(MINIMUM_PYTHON_VERSION),
            "implementation": platform.python_implementation(),
            "platformMachine": platform.machine(),
            "pointerBits": pointer_bits,
            "abiflags": getattr(sys, "abiflags", ""),
            "soabi": sysconfig.get_config_var("SOABI"),
            "extensionSuffix": sysconfig.get_config_var("EXT_SUFFIX"),
            "prefix": str(prefix),
            "basePrefix": str(base_prefix),
            "pe": python_pe,
        },
        "windows": {
            **os_architecture,
            **process_report,
            "isElevated": elevated,
        },
        "conda": conda,
        "environment": {
            "pythonHome": os.environ.get("PYTHONHOME"),
            "pythonPath": os.environ.get("PYTHONPATH"),
            "condaPathEntries": path_conda_entries,
        },
        "errors": errors,
        "warnings": warnings,
    }

    if mode == "full":
        distributions: list[dict[str, object]] = []
        for name in REQUIRED_BINARY_DISTRIBUTIONS:
            try:
                distribution = _distribution_report(name, expected)
            except importlib.metadata.PackageNotFoundError:
                errors.append(f"Required build distribution is not installed: {name}.")
                continue
            distributions.append(distribution)
            if not distribution["matchesArchitecture"]:
                errors.append(
                    f"{distribution['name']} {distribution['version']} has incompatible "
                    "wheel tags: "
                    f"{distribution['tags']}."
                )
        for name in OPTIONAL_BINARY_DISTRIBUTIONS:
            try:
                distribution = _distribution_report(name, expected)
            except importlib.metadata.PackageNotFoundError:
                continue
            distributions.append(distribution)
            if not distribution["matchesArchitecture"]:
                errors.append(
                    f"{distribution['name']} {distribution['version']} has incompatible "
                    "wheel tags: "
                    f"{distribution['tags']}."
                )
        report["distributions"] = distributions

        isolated_checks: list[dict[str, object]] = []
        qt_result, qt_check = _run_json_child("PySide6 and Qt WebEngine imports", QT_IMPORT_SCRIPT)
        isolated_checks.append(qt_check)
        win32_result, win32_check = _run_json_child(
            "pywin32 and comtypes imports", WIN32_IMPORT_SCRIPT
        )
        isolated_checks.append(win32_check)
        hook_result, hook_check = _run_json_child(
            "PyInstaller Qt library information", QT_HOOK_SCRIPT
        )
        isolated_checks.append(hook_check)
        report["isolatedChecks"] = isolated_checks
        for check, result in (
            (qt_check, qt_result),
            (win32_check, win32_result),
            (hook_check, hook_result),
        ):
            if result is None:
                diagnostics = (
                    check.get("stderr")
                    or check.get("parseError")
                    or "No diagnostics were produced."
                )
                errors.append(
                    f"Isolated check failed: {check['name']} (exit {check['returnCode']}). "
                    f"{diagnostics}"
                )

        critical_paths: list[Path] = [python_executable]
        python_dlls = find_python_dlls(
            (prefix, base_prefix), major=sys.version_info.major, minor=sys.version_info.minor
        )
        if not python_dlls:
            errors.append("The active Python shared library could not be located.")
        critical_paths.extend(python_dlls)

        if qt_result is not None:
            qt_modules = cast(dict[str, str], qt_result["modules"])
            qt_binaries = cast(dict[str, str], qt_result["binaries"])
            critical_paths.extend(Path(path) for path in qt_modules.values())
            critical_paths.extend(Path(path) for path in qt_binaries.values())
        if win32_result is not None:
            win32_modules = cast(dict[str, str], win32_result)
            critical_paths.extend(
                Path(path)
                for name, path in win32_modules.items()
                if name != "comtypes" and Path(path).suffix.lower() in {".dll", ".pyd"}
            )

        pyinstaller_spec = importlib.util.find_spec("PyInstaller")
        if pyinstaller_spec is None or pyinstaller_spec.origin is None:
            errors.append("PyInstaller could not be located after its wheel metadata was read.")
        else:
            pyinstaller_root = Path(pyinstaller_spec.origin).resolve().parent
            bootloaders = sorted(
                pyinstaller_root.joinpath("bootloader").rglob("run*.exe"),
                key=lambda path: str(path).casefold(),
            )
            if not bootloaders:
                errors.append("No PyInstaller Windows bootloader executables were found.")
            critical_paths.extend(bootloaders)
            report["pyinstallerBootloaders"] = [str(path) for path in bootloaders]

        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve()
        allowed_roots = [prefix, base_prefix, system_root]
        critical_entries, critical_errors = _critical_binary_report(
            critical_paths, expected, allowed_roots
        )
        report["criticalBinaries"] = critical_entries
        errors.extend(critical_errors)

        external_dlls = find_external_critical_dlls(
            os.environ.get("PATH", ""), allowed_roots=allowed_roots
        )
        report["externalCriticalDllsOnPath"] = [str(path) for path in external_dlls]
        if external_dlls:
            errors.append(
                "PATH exposes unrelated native runtime candidates: "
                + ", ".join(str(path) for path in external_dlls)
                + "."
            )

        if payload is not None:
            payload_report = verify_pe_paths([payload], expected=expected, recursive=True)
            report["payload"] = payload_report
            errors.extend(cast(list[str], payload_report["errors"]))

    report["status"] = "pass" if not errors else "fail"
    return report


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", required=True, choices=("x64",))
    parser.add_argument(
        "--mode",
        required=True,
        choices=("base", "full"),
        help="Use base before dependency installation and full before or after packaging.",
    )
    parser.add_argument("--report", type=Path, help="Optional JSON report output path.")
    parser.add_argument(
        "--payload",
        type=Path,
        help="Optional frozen bundle directory to scan recursively in full mode.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the build-environment verifier."""
    args = build_parser().parse_args(argv)
    if args.payload is not None and args.mode != "full":
        raise SystemExit("--payload requires --mode full.")
    report = verify_environment(
        architecture=args.architecture,
        mode=args.mode,
        payload=args.payload,
    )
    if args.report is not None:
        _write_report(args.report, report)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
