"""Audit PyInstaller analysis evidence, binary provenance, and payload architecture."""

from __future__ import annotations

import argparse
import ast
import codecs
import json
import os
import re
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from scripts.pe_arch import normalize_architecture, verify_pe_paths
elif __package__:
    from .pe_arch import normalize_architecture, verify_pe_paths
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pe_arch import normalize_architecture, verify_pe_paths


REQUIRED_ANALYSIS_FILES = (
    "warn-Fenestra.txt",
    "xref-Fenestra.html",
    "Analysis-00.toc",
    "COLLECT-00.toc",
)
NATIVE_TOC_TYPES = frozenset({"BINARY", "EXTENSION"})
FATAL_TRANSCRIPT_PATTERNS = (
    re.compile(r"QtLibraryInfo\(PySide6\).*failed to obtain Qt library info", re.IGNORECASE),
    re.compile(r"failed to obtain Qt library info", re.IGNORECASE),
    re.compile(r"DLL load failed while importing QtCore", re.IGNORECASE),
)
PROHIBITED_COMPONENTS = frozenset({"mfc140u.dll", "pythonwin", "win32ui.pyd"})
PYINSTALLER_WARNFILE_HEADER = """\

This file lists modules PyInstaller was not able to find. This does not
necessarily mean these modules are required for running your program. Both
Python's standard library and 3rd-party Python packages often conditionally
import optional modules, some of which may be available only on certain
platforms.

Types of import:
* top-level: imported at the top-level - look at these first
* conditional: imported within an if-statement
* delayed: imported within a function
* optional: imported within a try-except-statement

IMPORTANT: Do NOT post this list to the issue-tracker. Use it as a basis for
            tracking down the missing module yourself. Thanks!

"""
MODULE_WARNING_PATTERN = re.compile(
    r"^(?P<status>[a-z]+) module named (?P<module>.+?) - imported by (?P<importers>.+)$"
)
IMPORTER_PATTERN = re.compile(r"(?:^|, )(?P<name>.+?) \((?P<modes>[^()]*)\)(?=, |$)")
TRANSCRIPT_WARNING_PATTERN = re.compile(r"^\s*(?:\d+\s+)?WARNING:\s*(?P<message>.+?)\s*$")
LIBRARY_DEPENDENCY_WARNING_PATTERN = re.compile(
    r"^Library not found: could not resolve (?P<library>.+?), "
    r"dependency of (?P<referrer>.+?)\.$"
)
CTYPES_LIBRARY_WARNING_PATTERN = re.compile(
    r"^Library (?P<library>.+?) required via ctypes not found$"
)
QML_PLUGIN_WARNING_PATTERN = re.compile(
    r"^QtLibraryInfo\(PySide6\): QML plugin binary (?P<plugin>.+?) does not exist!$"
)
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
WINDOWS_API_SET_PATTERN = re.compile(r"^(?:api|ext)-ms-win-[a-z0-9_-]+\.dll$", re.IGNORECASE)
WINDOWS_SYSTEM_LIBRARIES = frozenset(
    {
        "advapi32.dll",
        "bcrypt.dll",
        "bcryptprimitives.dll",
        "cabinet.dll",
        "cfgmgr32.dll",
        "comctl32.dll",
        "comdlg32.dll",
        "crypt32.dll",
        "cryptbase.dll",
        "cryptnet.dll",
        "cryptsp.dll",
        "d2d1.dll",
        "d3d11.dll",
        "d3d12.dll",
        "dcomp.dll",
        "dnsapi.dll",
        "dwmapi.dll",
        "dwrite.dll",
        "dxgi.dll",
        "dxva2.dll",
        "gdi32.dll",
        "imm32.dll",
        "iphlpapi.dll",
        "kernel32",
        "kernel32.dll",
        "mpr.dll",
        "msvcrt.dll",
        "ncrypt.dll",
        "netapi32.dll",
        "normaliz.dll",
        "ntdll.dll",
        "ole32.dll",
        "oleacc.dll",
        "oleaut32.dll",
        "opengl32.dll",
        "powrprof.dll",
        "propsys.dll",
        "psapi.dll",
        "rpcrt4.dll",
        "secur32.dll",
        "setupapi.dll",
        "shcore.dll",
        "shell32.dll",
        "shlwapi.dll",
        "ucrtbase.dll",
        "user32.dll",
        "userenv.dll",
        "usp10.dll",
        "uxtheme.dll",
        "version.dll",
        "winhttp.dll",
        "wininet.dll",
        "winmm.dll",
        "winspool.drv",
        "wintrust.dll",
        "ws2_32.dll",
        "wtsapi32.dll",
    }
)
BENIGN_MISSING_MODULE_RULES: dict[str, tuple[frozenset[str] | None, str, str]] = {
    # The pathlib importer names cover both stdlib layouts: Python 3.13 keeps
    # the POSIX branches in pathlib._local, while Python 3.14 imports them from
    # the pathlib package and the new pathlib._os helper module.
    "pwd": (
        frozenset(
            {"posixpath", "shutil", "tarfile", "pathlib", "pathlib._local", "subprocess", "netrc"}
        ),
        "foreign-platform-optional-import",
        "The module is POSIX-only, and every importer is a reviewed "
        "cross-platform standard-library branch.",
    ),
    "grp": (
        frozenset({"shutil", "tarfile", "pathlib", "pathlib._local", "subprocess"}),
        "foreign-platform-optional-import",
        "The module is POSIX-only, and every importer is a reviewed "
        "cross-platform standard-library branch.",
    ),
    "posix": (
        frozenset({"os", "posixpath", "shutil", "importlib._bootstrap_external", "pathlib._os"}),
        "foreign-platform-optional-import",
        "The POSIX implementation is unavailable by design in a Windows build, and every "
        "importer is a reviewed standard-library platform branch.",
    ),
    "_scproxy": (
        frozenset({"urllib.request"}),
        "foreign-platform-optional-import",
        "The module is the macOS proxy-resolution branch of urllib.request and does not "
        "exist on Windows.",
    ),
    "resource": (
        frozenset({"posix"}),
        "foreign-platform-optional-import",
        "The resource module belongs to the unavailable POSIX branch.",
    ),
    "_posixsubprocess": (
        frozenset({"subprocess"}),
        "foreign-platform-optional-import",
        "The extension belongs to subprocess's POSIX implementation and is not loaded on Windows.",
    ),
    "fcntl": (
        frozenset({"subprocess", "pathlib._os", "keyboard._nixcommon"}),
        "foreign-platform-optional-import",
        "The module is used only by reviewed POSIX standard-library and keyboard branches.",
    ),
    "AppKit": (
        frozenset({"keyboard._darwinkeyboard"}),
        "foreign-platform-optional-import",
        "The module is used only by keyboard's macOS backend.",
    ),
    "Quartz": (
        frozenset({"keyboard._darwinkeyboard"}),
        "foreign-platform-optional-import",
        "The module is used only by keyboard's macOS backend.",
    ),
    "vms_lib": (
        frozenset({"platform"}),
        "foreign-platform-optional-import",
        "The module is an optional OpenVMS branch in the standard-library platform module.",
    ),
    "java.lang": (
        frozenset({"platform"}),
        "foreign-platform-optional-import",
        "The module is an optional Java-runtime branch in the standard-library platform module.",
    ),
    "java": (
        frozenset({"platform"}),
        "foreign-platform-optional-import",
        "The module is an optional Java-runtime branch in the standard-library platform module.",
    ),
    "Queue": (
        frozenset({"keyboard", "keyboard._nixcommon", "keyboard._generic"}),
        "legacy-optional-import",
        "The Python 2 compatibility name is optional in keyboard and is replaced by queue "
        "on supported Python.",
    ),
    "_frozen_importlib_external": (
        frozenset({"importlib._bootstrap", "importlib", "importlib.abc"}),
        "python-runtime-pseudo-module",
        "The frozen importlib implementation is supplied by the active Python runtime rather "
        "than a file module.",
    ),
    "ctypes._FuncPointer": (
        frozenset({"ctypes", "comtypes._vtbl"}),
        "python-runtime-pseudo-module",
        "The ctypes implementation supplies this private runtime type dynamically.",
    ),
    "ctypes._CDataType": (
        frozenset({"ctypes", "comtypes._memberspec", "comtypes.automation"}),
        "python-runtime-pseudo-module",
        "The ctypes implementation supplies this private runtime type dynamically.",
    ),
    "ctypes._CArgObject": (
        frozenset(
            {
                "ctypes",
                "comtypes._memberspec",
                "comtypes.automation",
                "comtypes._comobject",
                "comtypes.messageloop",
                "comtypes.connectionpoints",
            }
        ),
        "python-runtime-pseudo-module",
        "The ctypes implementation supplies this private runtime type dynamically.",
    ),
    "ctypes._CData": (
        frozenset({"ctypes", "comtypes"}),
        "python-runtime-pseudo-module",
        "The ctypes implementation supplies this private runtime type dynamically.",
    ),
    "win32com.gen_py": (
        frozenset({"win32com"}),
        "generated-optional-import",
        "win32com.gen_py is a generated COM-wrapper cache, not a distributable source module.",
    ),
    "pywin.dialogs": (
        frozenset({"win32com.client.selecttlb", "win32com.client.makepy"}),
        "unused-pythonwin-ui",
        "Only win32com's delayed MakePy UI path imports pywin.dialogs; Fenestra uses Dispatch and "
        "intentionally excludes the Pythonwin UI graph.",
    ),
    "numpy.ctypeslib": (
        frozenset({"comtypes._npsupport"}),
        "unused-optional-integration",
        "Only comtypes' delayed optional NumPy integration imports this module; Fenestra does "
        "not depend on NumPy.",
    ),
    "numpy": (
        frozenset({"comtypes._npsupport"}),
        "unused-optional-integration",
        "Only comtypes' delayed optional NumPy integration imports this module; Fenestra does "
        "not depend on NumPy.",
    ),
    "collections.abc": (
        None,
        "python-modulegraph-alias",
        "collections.abc is supplied by the selected Python standard library; this is a "
        "reviewed modulegraph alias warning.",
    ),
}


def _write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _normalized_path(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _is_below(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath(
            (_normalized_path(path), _normalized_path(root))
        ) == _normalized_path(root)
    except (OSError, ValueError):
        return False


def _load_toc(path: Path) -> object:
    try:
        return ast.literal_eval(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        raise ValueError(f"Could not parse PyInstaller table of contents {path}: {exc}.") from exc


def _read_powershell_transcript(path: Path) -> str:
    """Read transcripts emitted by Windows PowerShell or modern PowerShell."""
    data = path.read_bytes()
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig", errors="replace")
    if b"\0" in data[:256]:
        return data.decode("utf-16-le", errors="replace")
    return data.decode("utf-8", errors="replace")


def _warning_record(
    *,
    source: str,
    line: int,
    raw: str,
    kind: str,
    subject: str,
    classification: str,
    category: str,
    reason: str,
) -> dict[str, object]:
    return {
        "source": source,
        "line": line,
        "raw": raw,
        "kind": kind,
        "subject": subject,
        "classification": classification,
        "category": category,
        "reason": reason,
    }


def _parse_importers(value: str) -> list[str] | None:
    matches = list(IMPORTER_PATTERN.finditer(value))
    if not matches:
        return None
    reconstructed = ", ".join(
        f"{match.group('name')} ({match.group('modes')})" for match in matches
    )
    if reconstructed != value:
        return None
    return [match.group("name") for match in matches]


def _classify_module_warning(
    *,
    source: str,
    line: int,
    raw: str,
    status: str,
    module: str,
    importer_text: str,
) -> dict[str, object]:
    importers = _parse_importers(importer_text)
    if importers is None:
        record = _warning_record(
            source=source,
            line=line,
            raw=raw,
            kind="module",
            subject=module,
            classification="fatal",
            category="unparsed-module-importers",
            reason="The importer list does not match PyInstaller's reviewed warning format.",
        )
        record["status"] = status
        record["importers"] = []
        return record

    normalized_module = module.strip("'\"")
    if status == "excluded" and normalized_module in {
        "_frozen_importlib",
        "pywin",
        "win32ui",
    }:
        reason = (
            "The frozen importlib implementation is supplied by Python itself."
            if normalized_module == "_frozen_importlib"
            else "Fenestra intentionally excludes the unused Pythonwin UI graph and verifies "
            "that no payload remnants remain."
        )
        record = _warning_record(
            source=source,
            line=line,
            raw=raw,
            kind="module",
            subject=normalized_module,
            classification="benign",
            category="reviewed-exclusion",
            reason=reason,
        )
    elif status != "missing":
        record = _warning_record(
            source=source,
            line=line,
            raw=raw,
            kind="module",
            subject=normalized_module,
            classification="fatal",
            category="unreviewed-module-status",
            reason=f"The PyInstaller module status {status!r} is not an approved release warning.",
        )
    elif normalized_module in BENIGN_MISSING_MODULE_RULES:
        allowed_importers, category, reason = BENIGN_MISSING_MODULE_RULES[normalized_module]
        unexpected_importers = (
            sorted(set(importers) - allowed_importers) if allowed_importers is not None else []
        )
        if unexpected_importers:
            record = _warning_record(
                source=source,
                line=line,
                raw=raw,
                kind="module",
                subject=normalized_module,
                classification="fatal",
                category="unreviewed-module-importer",
                reason=(
                    "A normally benign optional module is imported by unreviewed modules: "
                    + ", ".join(unexpected_importers)
                    + "."
                ),
            )
        else:
            record = _warning_record(
                source=source,
                line=line,
                raw=raw,
                kind="module",
                subject=normalized_module,
                classification="benign",
                category=category,
                reason=reason,
            )
    else:
        lowered = normalized_module.casefold()
        if lowered == "win32ui" or lowered.startswith(("mfc", "pythonwin")):
            category = "missing-mfc-runtime"
            reason = "A missing Pythonwin or MFC module is an unsupported runtime dependency."
        elif lowered.startswith(("pyside6", "shiboken6", "qt")):
            category = "missing-qt-runtime"
            reason = "A required or unreviewed Qt runtime module is missing."
        elif lowered.startswith(("openssl", "cryptography")):
            category = "missing-openssl-runtime"
            reason = "An OpenSSL-dependent Python runtime module is missing."
        elif lowered.startswith(
            ("pythoncom", "pywintypes", "win32api", "win32event", "win32gui", "comtypes")
        ) or lowered in {"_ssl", "ssl", "_hashlib", "encodings", "zipimport"}:
            category = "missing-python-runtime"
            reason = "A required Python or Win32 extension module is missing."
        elif lowered == "fenestra" or lowered.startswith("fenestra."):
            category = "missing-application-module"
            reason = "A Fenestra application module is missing from the frozen graph."
        else:
            category = "unreviewed-optional-import"
            reason = (
                "This missing module has no reviewed benign rule, even if an importer marks "
                "it optional."
            )
        record = _warning_record(
            source=source,
            line=line,
            raw=raw,
            kind="module",
            subject=normalized_module,
            classification="fatal",
            category=category,
            reason=reason,
        )

    record["status"] = status
    record["importers"] = importers
    return record


def _parse_warning_file(path: Path) -> list[dict[str, object]]:
    source = path.name
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [
            _warning_record(
                source=source,
                line=1,
                raw="<unreadable warning file>",
                kind="format",
                subject=source,
                classification="fatal",
                category="unreadable-warning-file",
                reason=f"The PyInstaller warning file could not be read as UTF-8: {exc}.",
            )
        ]

    records: list[dict[str, object]] = []
    if text.startswith(PYINSTALLER_WARNFILE_HEADER):
        first_line = PYINSTALLER_WARNFILE_HEADER.count("\n") + 1
        candidate_lines = enumerate(
            text[len(PYINSTALLER_WARNFILE_HEADER) :].splitlines(), start=first_line
        )
    else:
        records.append(
            _warning_record(
                source=source,
                line=1,
                raw=text.splitlines()[0] if text.splitlines() else "<empty warning file>",
                kind="format",
                subject=source,
                classification="fatal",
                category="unrecognized-warning-file-header",
                reason="The file does not begin with the pinned PyInstaller warning-file header.",
            )
        )
        candidate_lines = enumerate(text.splitlines(), start=1)

    for line_number, raw_line in candidate_lines:
        line_text = raw_line.strip()
        if not line_text:
            continue
        match = MODULE_WARNING_PATTERN.fullmatch(line_text)
        if match is None:
            if not text.startswith(PYINSTALLER_WARNFILE_HEADER):
                continue
            records.append(
                _warning_record(
                    source=source,
                    line=line_number,
                    raw=raw_line,
                    kind="format",
                    subject="unparsed record",
                    classification="fatal",
                    category="unparsed-warning-file-record",
                    reason=(
                        "The nonempty record does not match PyInstaller's module-warning format."
                    ),
                )
            )
            continue
        records.append(
            _classify_module_warning(
                source=source,
                line=line_number,
                raw=raw_line,
                status=match.group("status"),
                module=match.group("module"),
                importer_text=match.group("importers"),
            )
        )
    return records


def _unquote_warning_value(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def _classify_library_warning(
    *,
    source: str,
    line: int,
    raw: str,
    library_value: str,
    referrer: str | None,
) -> dict[str, object]:
    library = _unquote_warning_value(library_value)
    basename = PureWindowsPath(library).name.casefold()
    if basename.startswith(("qt", "pyside", "shiboken", "qwindows")) or basename.startswith("icu"):
        classification = "fatal"
        category = "missing-qt-runtime"
        reason = "A Qt, PySide, Shiboken, or ICU runtime library is missing."
    elif re.fullmatch(r"py(?:thon(?:com(?:loader)?)?|wintypes)\d+\.dll", basename):
        classification = "fatal"
        category = "missing-python-runtime"
        reason = "The selected Python or pywin32 runtime library is missing."
    elif basename.startswith(("mfc", "msvcp", "vcruntime", "concrt", "vcomp")):
        classification = "fatal"
        category = "missing-mfc-or-vc-runtime"
        reason = "A required MFC or Visual C++ redistributable library is missing."
    elif basename.startswith(("libssl", "libcrypto", "ssleay", "libeay")):
        classification = "fatal"
        category = "missing-openssl-runtime"
        reason = "An OpenSSL runtime library is missing; the build must not borrow one from PATH."
    elif WINDOWS_API_SET_PATTERN.fullmatch(basename):
        classification = "benign"
        category = "windows-api-set-resolution"
        reason = (
            "The name is a Windows API-set contract resolved by the Windows 11 loader and "
            "must not be bundled."
        )
    elif basename in WINDOWS_SYSTEM_LIBRARIES:
        classification = "benign"
        category = "windows-system-resolution"
        reason = (
            "The library is a reviewed Windows 11 system component resolved by the "
            "operating-system loader."
        )
    else:
        classification = "fatal"
        category = "missing-non-system-runtime"
        reason = (
            "The missing library is not an approved Windows system component or API-set contract."
        )

    record = _warning_record(
        source=source,
        line=line,
        raw=raw,
        kind="library",
        subject=library,
        classification=classification,
        category=category,
        reason=reason,
    )
    if referrer is not None:
        record["referrer"] = _unquote_warning_value(referrer)
    return record


def _classify_qml_plugin_warning(
    *,
    source: str,
    line: int,
    raw: str,
    plugin_value: str,
    python_prefix: Path,
) -> dict[str, object]:
    plugin = _unquote_warning_value(plugin_value)
    plugin_path = Path(plugin)
    parts = tuple(part.casefold() for part in PureWindowsPath(plugin).parts)
    expected_tail = (
        "pyside6",
        "qml",
        "qt",
        "labs",
        "assetdownloader",
        "qmlassetdownloaderprivateplugin.dll",
    )
    qmldir = plugin_path.parent / "qmldir"
    optional_declaration = False
    if qmldir.is_file():
        qmldir_lines = {
            line.strip().casefold()
            for line in qmldir.read_text(encoding="utf-8", errors="replace").splitlines()
        }
        optional_declaration = "optional plugin qmlassetdownloaderprivateplugin" in qmldir_lines
    justified = (
        parts[-len(expected_tail) :] == expected_tail
        and plugin_path.is_absolute()
        and _is_below(plugin_path, python_prefix)
        and not plugin_path.exists()
        and optional_declaration
    )
    if justified:
        classification = "benign"
        category = "optional-pyside6-qml-plugin"
        reason = (
            "PySide6's adjacent qmldir explicitly marks qmlassetdownloaderprivateplugin "
            "optional; Fenestra uses its React frontend through Qt WebEngine and does not "
            "use this QML feature."
        )
    else:
        classification = "fatal"
        category = "missing-qt-runtime"
        reason = (
            "The missing QML plugin is not the exact PySide6 assetdownloader plugin with a "
            "verified optional qmldir declaration."
        )
    record = _warning_record(
        source=source,
        line=line,
        raw=raw,
        kind="qml-plugin",
        subject=plugin,
        classification=classification,
        category=category,
        reason=reason,
    )
    record["qmldir"] = str(qmldir)
    record["optionalDeclarationVerified"] = optional_declaration
    return record


def _parse_transcript_warnings(
    transcript_text: str,
    *,
    transcript: Path,
    python_prefix: Path,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(transcript_text.splitlines(), start=1):
        normalized_line = ANSI_ESCAPE_PATTERN.sub("", raw_line)
        warning_match = TRANSCRIPT_WARNING_PATTERN.fullmatch(normalized_line)
        if warning_match is None:
            continue
        message = warning_match.group("message")
        dependency_match = LIBRARY_DEPENDENCY_WARNING_PATTERN.fullmatch(message)
        ctypes_match = CTYPES_LIBRARY_WARNING_PATTERN.fullmatch(message)
        qml_match = QML_PLUGIN_WARNING_PATTERN.fullmatch(message)
        if dependency_match is not None:
            record = _classify_library_warning(
                source=transcript.name,
                line=line_number,
                raw=raw_line,
                library_value=dependency_match.group("library"),
                referrer=dependency_match.group("referrer"),
            )
        elif ctypes_match is not None:
            record = _classify_library_warning(
                source=transcript.name,
                line=line_number,
                raw=raw_line,
                library_value=ctypes_match.group("library"),
                referrer=None,
            )
        elif qml_match is not None:
            record = _classify_qml_plugin_warning(
                source=transcript.name,
                line=line_number,
                raw=raw_line,
                plugin_value=qml_match.group("plugin"),
                python_prefix=python_prefix,
            )
        else:
            record = _warning_record(
                source=transcript.name,
                line=line_number,
                raw=raw_line,
                kind="transcript",
                subject=message,
                classification="fatal",
                category="unreviewed-pyinstaller-warning",
                reason="The PyInstaller warning has no reviewed benign classification rule.",
            )
        records.append(record)
    return records


def _iter_toc_entries(value: object) -> Iterator[tuple[str, str, str]]:
    if isinstance(value, (list, tuple)):
        if (
            len(value) == 3
            and all(isinstance(item, str) for item in value)
            and value[2] in {"BINARY", "DATA", "EXECUTABLE", "EXTENSION", "PYMODULE", "PYSOURCE"}
        ):
            yield value[0], value[1], value[2]
            return
        for item in value:
            yield from _iter_toc_entries(item)


def _has_prohibited_component(value: str) -> bool:
    components = {component.casefold() for component in re.split(r"[\\/]", value) if component}
    return bool(components & PROHIBITED_COMPONENTS)


def _default_windows_roots() -> list[Path]:
    roots: dict[str, Path] = {}
    for variable in ("SystemRoot", "WINDIR"):
        value = os.environ.get(variable)
        if value:
            path = Path(value).resolve()
            roots[_normalized_path(path)] = path
    return sorted(roots.values(), key=lambda path: _normalized_path(path))


def audit_pyinstaller(
    *,
    architecture: str,
    build_dir: Path,
    bundle: Path,
    python_prefix: Path,
    python_base_prefix: Path,
    transcript: Path,
    windows_roots: Sequence[Path] | None = None,
) -> dict[str, object]:
    """Audit one completed PyInstaller build and return a JSON-ready report."""
    expected = normalize_architecture(architecture)
    build_dir = build_dir.resolve()
    bundle = bundle.resolve()
    python_prefix = python_prefix.resolve()
    python_base_prefix = python_base_prefix.resolve()
    transcript = transcript.resolve()
    selected_windows_roots = [
        path.resolve() for path in (windows_roots or _default_windows_roots())
    ]
    approved_roots = [python_prefix, python_base_prefix, *selected_windows_roots]
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, object]] = []

    required_paths = {name: build_dir / name for name in REQUIRED_ANALYSIS_FILES}
    missing_artifacts = [
        str(path)
        for path in required_paths.values()
        if not path.is_file() or path.stat().st_size == 0
    ]
    if missing_artifacts:
        errors.append(
            "Required nonempty PyInstaller analysis artifacts are missing: "
            + ", ".join(missing_artifacts)
            + "."
        )
    checks.append(
        {
            "name": "PyInstaller analysis artifacts",
            "required": [str(path) for path in required_paths.values()],
            "missingOrEmpty": missing_artifacts,
        }
    )

    transcript_matches: list[dict[str, str]] = []
    transcript_text: str | None = None
    if not transcript.is_file() or transcript.stat().st_size == 0:
        errors.append(f"The nonempty PyInstaller transcript is missing: {transcript}.")
    else:
        transcript_text = _read_powershell_transcript(transcript)
        for pattern in FATAL_TRANSCRIPT_PATTERNS:
            match = pattern.search(transcript_text)
            if match is not None:
                transcript_matches.append({"pattern": pattern.pattern, "match": match.group(0)})
    if transcript_matches:
        errors.append(
            "The PyInstaller transcript contains a fatal PySide6 Qt hook/import failure: "
            + "; ".join(match["match"] for match in transcript_matches)
            + "."
        )
    checks.append(
        {
            "name": "Fatal PySide6 transcript failures",
            "transcript": str(transcript),
            "matches": transcript_matches,
        }
    )

    warning_records: list[dict[str, object]] = []
    warning_file = required_paths["warn-Fenestra.txt"]
    if warning_file.is_file() and warning_file.stat().st_size > 0:
        warning_records.extend(_parse_warning_file(warning_file))
    if transcript_text is not None:
        warning_records.extend(
            _parse_transcript_warnings(
                transcript_text,
                transcript=transcript,
                python_prefix=python_prefix,
            )
        )
    fatal_warning_records = [
        record for record in warning_records if record["classification"] == "fatal"
    ]
    benign_warning_records = [
        record for record in warning_records if record["classification"] == "benign"
    ]
    errors.extend(
        (
            f"Fatal PyInstaller warning at {record['source']}:{record['line']} "
            f"[{record['category']}]: {record['subject']}. {record['reason']}"
        )
        for record in fatal_warning_records
    )
    warnings.extend(
        f"{record['source']}:{record['line']} [{record['category']}]: {record['reason']}"
        for record in benign_warning_records
    )
    checks.append(
        {
            "name": "PyInstaller warning classification",
            "warningFile": str(warning_file),
            "recordCount": len(warning_records),
            "benignCount": len(benign_warning_records),
            "fatalCount": len(fatal_warning_records),
            "records": warning_records,
        }
    )

    toc_entries: list[dict[str, str]] = []
    toc_parse_errors: list[str] = []
    for name in ("Analysis-00.toc", "COLLECT-00.toc"):
        path = required_paths[name]
        if not path.is_file():
            continue
        try:
            entries = list(_iter_toc_entries(_load_toc(path)))
        except ValueError as exc:
            toc_parse_errors.append(str(exc))
            continue
        for destination, source_text, entry_type in entries:
            toc_entries.append(
                {
                    "table": name,
                    "destination": destination,
                    "source": source_text,
                    "type": entry_type,
                }
            )
    errors.extend(toc_parse_errors)
    checks.append(
        {
            "name": "PyInstaller table parsing",
            "entryCount": len(toc_entries),
            "errors": toc_parse_errors,
        }
    )

    prohibited_toc_entries = [
        entry
        for entry in toc_entries
        if _has_prohibited_component(entry["destination"])
        or _has_prohibited_component(entry["source"])
    ]
    prohibited_bundle_files: list[str] = []
    if bundle.is_dir():
        prohibited_bundle_files = sorted(
            path.relative_to(bundle).as_posix()
            for path in bundle.rglob("*")
            if path.is_file() and _has_prohibited_component(str(path.relative_to(bundle)))
        )
    if prohibited_toc_entries or prohibited_bundle_files:
        errors.append("Pythonwin, win32ui, or MFC remnants remain in the PyInstaller output.")
    checks.append(
        {
            "name": "Unused Pythonwin and MFC exclusion",
            "tocEntries": prohibited_toc_entries,
            "bundleFiles": prohibited_bundle_files,
        }
    )

    native_entries = [entry for entry in toc_entries if entry["type"] in NATIVE_TOC_TYPES]
    external_origins: list[dict[str, str]] = []
    native_origin_paths: dict[str, Path] = {}
    for entry in native_entries:
        origin_path = Path(entry["source"])
        native_origin_paths[_normalized_path(origin_path)] = origin_path
        if not origin_path.is_absolute() or not any(
            _is_below(origin_path, root) for root in approved_roots
        ):
            external_origins.append(entry)
    if external_origins:
        errors.append(
            "Native PyInstaller inputs were resolved outside the selected Python "
            "and Windows roots: "
            + ", ".join(sorted({entry["source"] for entry in external_origins}))
            + "."
        )
    checks.append(
        {
            "name": "Native input provenance",
            "approvedRoots": [str(path) for path in approved_roots],
            "nativeEntryCount": len(native_entries),
            "uniqueOriginCount": len(native_origin_paths),
            "externalOrigins": external_origins,
        }
    )

    native_origin_report = verify_pe_paths(
        list(native_origin_paths.values()), expected=expected, recursive=False
    )
    if native_entries:
        errors.extend(cast(list[str], native_origin_report["errors"]))
    else:
        errors.append("No native BINARY or EXTENSION entries were found in the PyInstaller TOCs.")
    checks.append(
        {
            "name": "Native input PE architecture",
            "checkedFileCount": native_origin_report["checkedFileCount"],
            "status": native_origin_report["status"],
        }
    )

    payload_report = verify_pe_paths([bundle], expected=expected, recursive=True)
    errors.extend(cast(list[str], payload_report["errors"]))
    checks.append(
        {
            "name": "Frozen payload PE architecture",
            "checkedFileCount": payload_report["checkedFileCount"],
            "status": payload_report["status"],
        }
    )

    return {
        "schemaVersion": 1,
        "requestedArchitecture": expected,
        "buildDirectory": str(build_dir),
        "bundle": str(bundle),
        "pythonPrefix": str(python_prefix),
        "pythonBasePrefix": str(python_base_prefix),
        "windowsRoots": [str(path) for path in selected_windows_roots],
        "status": "pass" if not errors else "fail",
        "checks": checks,
        "nativeOriginArchitecture": native_origin_report,
        "payloadArchitecture": payload_report,
        "warningClassifications": warning_records,
        "errors": errors,
        "warnings": warnings,
    }


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", required=True, choices=("x64",))
    parser.add_argument("--build-dir", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--python-prefix", required=True, type=Path)
    parser.add_argument("--python-base-prefix", required=True, type=Path)
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the post-PyInstaller audit."""
    args = build_parser().parse_args(argv)
    report = audit_pyinstaller(
        architecture=args.architecture,
        build_dir=args.build_dir,
        bundle=args.bundle,
        python_prefix=args.python_prefix,
        python_base_prefix=args.python_base_prefix,
        transcript=args.transcript,
    )
    _write_report(args.report, report)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
