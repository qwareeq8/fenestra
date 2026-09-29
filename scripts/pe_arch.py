"""Inspect and verify the architecture recorded in Windows PE files."""

from __future__ import annotations

import argparse
import json
import struct
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

PE_SUFFIXES = frozenset({".dll", ".exe", ".node", ".ocx", ".pyd"})
MACHINE_ARCHITECTURES = {
    0x014C: "x86",
    0x8664: "x64",
    0xAA64: "arm64",
}
ARCHITECTURE_MACHINES = {name: machine for machine, name in MACHINE_ARCHITECTURES.items()}


class PEFormatError(ValueError):
    """Raised when a file is not a structurally valid PE image."""


@dataclass(frozen=True)
class PEFile:
    """Describe the COFF Machine field from one PE image."""

    path: str
    machine: int
    machine_hex: str
    architecture: str


def normalize_architecture(value: str) -> str:
    """Normalize a supported Windows architecture name."""
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "x64": "x64",
        "amd64": "x64",
        "x86-64": "x64",
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86": "x86",
        "i386": "x86",
        "i686": "x86",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(ARCHITECTURE_MACHINES))
        raise ValueError(
            f"Unsupported architecture {value!r}. Expected one of: {supported}."
        ) from exc


def read_pe(path: str | Path) -> PEFile:
    """Read and validate the COFF Machine field from a PE image."""
    file_path = Path(path)
    try:
        size = file_path.stat().st_size
        with file_path.open("rb") as stream:
            if size < 0x40 or stream.read(2) != b"MZ":
                raise PEFormatError(f"{file_path} does not have a valid DOS header.")

            stream.seek(0x3C)
            offset_bytes = stream.read(4)
            if len(offset_bytes) != 4:
                raise PEFormatError(f"{file_path} has a truncated DOS header.")
            pe_offset = struct.unpack("<I", offset_bytes)[0]
            if pe_offset > size - 6:
                raise PEFormatError(f"{file_path} has an out-of-range PE header offset.")

            stream.seek(pe_offset)
            if stream.read(4) != b"PE\0\0":
                raise PEFormatError(f"{file_path} does not have a valid PE signature.")
            machine_bytes = stream.read(2)
            if len(machine_bytes) != 2:
                raise PEFormatError(f"{file_path} has a truncated COFF header.")
            machine = struct.unpack("<H", machine_bytes)[0]
    except OSError as exc:
        raise PEFormatError(f"Could not read {file_path}: {exc}.") from exc

    return PEFile(
        path=str(file_path.resolve()),
        machine=machine,
        machine_hex=f"0x{machine:04X}",
        architecture=MACHINE_ARCHITECTURES.get(machine, "unknown"),
    )


def collect_pe_paths(paths: Sequence[str | Path], *, recursive: bool) -> list[Path]:
    """Return deterministic PE file paths from explicit files and directories."""
    collected: dict[str, Path] = {}
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_file():
            collected[str(path.resolve()).casefold()] = path
            continue
        if not path.is_dir():
            raise FileNotFoundError(f"PE input does not exist: {path}.")

        iterator = path.rglob("*") if recursive else path.iterdir()
        for candidate in iterator:
            if candidate.is_file() and candidate.suffix.lower() in PE_SUFFIXES:
                collected[str(candidate.resolve()).casefold()] = candidate

    return sorted(collected.values(), key=lambda item: str(item.resolve()).casefold())


def verify_pe_paths(
    paths: Sequence[str | Path],
    *,
    expected: str | None,
    recursive: bool,
) -> dict[str, object]:
    """Inventory recognized PE images and optionally enforce one architecture."""
    architecture = normalize_architecture(expected) if expected is not None else None
    files: list[dict[str, object]] = []
    errors: list[str] = []

    try:
        selected = collect_pe_paths(paths, recursive=recursive)
    except (FileNotFoundError, OSError) as exc:
        selected = []
        errors.append(str(exc))

    if not selected and not errors:
        errors.append("No PE files were selected.")

    for path in selected:
        try:
            result = read_pe(path)
        except PEFormatError as exc:
            errors.append(str(exc))
            continue

        recognized = result.architecture != "unknown"
        matches = recognized and (architecture is None or result.architecture == architecture)
        entry = asdict(result)
        entry["recognized"] = recognized
        entry["matches"] = matches
        files.append(entry)
        if not recognized:
            errors.append(
                f"{result.path} has an unsupported PE Machine value {result.machine_hex}."
            )
        elif architecture is not None and not matches:
            errors.append(
                f"{result.path} is {result.architecture} ({result.machine_hex}); "
                f"expected {architecture}."
            )

    return {
        "schemaVersion": 1,
        "mode": "enforce" if architecture is not None else "inventory",
        "expectedArchitecture": architecture,
        "status": "pass" if not errors else "fail",
        "checkedFileCount": len(files),
        "files": files,
        "errors": errors,
    }


def _write_json(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expected",
        choices=("x64", "arm64"),
        help="Optional required architecture for every selected PE image.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively scan .exe, .dll, .pyd, .node, and .ocx files below directories.",
    )
    parser.add_argument("--json", type=Path, help="Optional JSON report output path.")
    parser.add_argument("paths", metavar="PATH", nargs="+", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the PE architecture verifier."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.recursive and args.expected is None:
        parser.error("--recursive requires --expected to prevent unenforced payload scans.")
    report = verify_pe_paths(args.paths, expected=args.expected, recursive=args.recursive)
    if args.json is not None:
        _write_json(args.json, report)

    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
