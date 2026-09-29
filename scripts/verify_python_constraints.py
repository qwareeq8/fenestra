"""Verify that a release environment exactly matches its Python constraints."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

_EXACT_CONSTRAINT = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;]+)$")
_NORMALIZE_NAME = re.compile(r"[-_.]+")


def normalize_distribution_name(name: str) -> str:
    """Return the normalized distribution key used for lock comparisons."""
    return _NORMALIZE_NAME.sub("-", name).casefold()


def parse_exact_constraints(text: str) -> dict[str, tuple[str, str]]:
    """Parse exact, unconditional constraints and reject ambiguous syntax."""
    constraints: dict[str, tuple[str, str]] = {}
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.partition("#")[0].strip()
        if not line:
            continue
        match = _EXACT_CONSTRAINT.fullmatch(line)
        if match is None:
            raise ValueError(
                f"Constraint line {line_number} is not an exact unconditional 'name==version' "
                f"pin: {raw_line!r}."
            )
        display_name = match.group("name")
        key = normalize_distribution_name(display_name)
        if key in constraints:
            raise ValueError(
                f"Constraint line {line_number} duplicates normalized package name {key!r}."
            )
        constraints[key] = (display_name, match.group("version"))
    if not constraints:
        raise ValueError("The constraints file contains no package pins.")
    return constraints


def installed_distributions() -> dict[str, tuple[str, str]]:
    """Return the active environment's installed distribution names and versions."""
    installed: dict[str, tuple[str, str]] = {}
    for distribution in importlib.metadata.distributions():
        display_name = distribution.metadata.get("Name")
        if not display_name:
            continue
        key = normalize_distribution_name(display_name)
        if key in installed:
            previous = installed[key]
            raise ValueError(
                f"Multiple installed distributions normalize to {key!r}: "
                f"{previous[0]!r} and {display_name!r}."
            )
        installed[key] = (display_name, distribution.version)
    return installed


def compare_environment(
    constraints: Mapping[str, tuple[str, str]],
    installed: Mapping[str, tuple[str, str]],
    allowed_unconstrained: Iterable[str],
) -> dict[str, object]:
    """Return exact-lock comparison evidence for the active environment."""
    allowed = {normalize_distribution_name(name) for name in allowed_unconstrained}
    constrained_installed = set(installed) - allowed
    unconstrained = sorted(constrained_installed - set(constraints))
    missing = sorted(set(constraints) - constrained_installed)
    mismatches = [
        {
            "package": installed[key][0],
            "installedVersion": installed[key][1],
            "constrainedVersion": constraints[key][1],
        }
        for key in sorted(constrained_installed & set(constraints))
        if installed[key][1] != constraints[key][1]
    ]
    return {
        "allowedUnconstrained": sorted(allowed),
        "unconstrainedInstalled": [installed[key][0] for key in unconstrained],
        "constraintsWithoutInstalledPackage": [constraints[key][0] for key in missing],
        "versionMismatches": mismatches,
        "success": not unconstrained and not missing and not mismatches,
    }


def _write_report(path: Path, report: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--constraints", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--allow-unconstrained",
        action="append",
        default=[],
        metavar="PACKAGE",
        help="Allow a bootstrap package that is not installed by the release dependency set.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Compare installed distributions with the exact constraints and return an exit code."""
    args = _parser().parse_args(argv)
    errors: list[str] = []
    try:
        constraints = parse_exact_constraints(args.constraints.read_text(encoding="utf-8"))
        installed = installed_distributions()
        comparison = compare_environment(
            constraints,
            installed,
            args.allow_unconstrained,
        )
    except (OSError, ValueError) as error:
        constraints = {}
        installed = {}
        comparison = {"success": False}
        errors.append(str(error))

    report: dict[str, object] = {
        "schemaVersion": 1,
        "constraintsPath": str(args.constraints.resolve()),
        "pythonExecutable": sys.executable,
        "constraints": {
            key: {"name": value[0], "version": value[1]}
            for key, value in sorted(constraints.items())
        },
        "installed": {
            key: {"name": value[0], "version": value[1]} for key, value in sorted(installed.items())
        },
        **comparison,
        "errors": errors,
    }
    try:
        _write_report(args.report, report)
    except OSError as error:
        print(f"ERROR: Could not write constraint evidence. {error}", file=sys.stderr)
        return 2

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if not bool(comparison["success"]):
        print(json.dumps(comparison, indent=2), file=sys.stderr)
        return 1

    print(
        f"Verified {len(constraints)} exact constraints against "
        f"{len(installed)} installed distributions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
