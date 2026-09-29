"""Tests for fail-closed Python constraint verification."""

from __future__ import annotations

import pytest

from scripts.verify_python_constraints import (
    compare_environment,
    normalize_distribution_name,
    parse_exact_constraints,
)


def test_distribution_names_are_normalized_consistently() -> None:
    """PEP-style spelling differences must identify the same distribution."""
    assert normalize_distribution_name("PySide6_Addons") == "pyside6-addons"
    assert normalize_distribution_name("pytest.cov") == "pytest-cov"


def test_constraints_require_exact_unconditional_pins() -> None:
    """Ranges and environment markers must not masquerade as a release lock."""
    with pytest.raises(ValueError, match="exact unconditional"):
        parse_exact_constraints("PySide6>=6.11\n")
    with pytest.raises(ValueError, match="exact unconditional"):
        parse_exact_constraints("pywin32==312; sys_platform == 'win32'\n")


def test_duplicate_normalized_names_are_rejected() -> None:
    """Equivalent spellings must not silently overwrite one another."""
    with pytest.raises(ValueError, match="duplicates normalized"):
        parse_exact_constraints("PySide6-Addons==6.11.1\nPySide6_Addons==6.11.1\n")


def test_comparison_requires_complete_matching_closure() -> None:
    """Unpinned, stale, and version-mismatched packages all fail verification."""
    comparison = compare_environment(
        {
            "pyside6": ("PySide6", "6.11.1"),
            "pywin32": ("pywin32", "312"),
            "stale": ("stale", "1.0"),
        },
        {
            "pip": ("pip", "26.1.2"),
            "fenestra": ("fenestra", "1.5.0"),
            "pyside6": ("PySide6", "6.11.1"),
            "pywin32": ("pywin32", "311"),
            "surprise": ("surprise", "2.0"),
        },
        ("pip", "fenestra"),
    )

    assert comparison["success"] is False
    assert comparison["unconstrainedInstalled"] == ["surprise"]
    assert comparison["constraintsWithoutInstalledPackage"] == ["stale"]
    assert comparison["versionMismatches"] == [
        {
            "package": "pywin32",
            "installedVersion": "311",
            "constrainedVersion": "312",
        }
    ]


def test_comparison_allows_only_explicit_bootstrap_packages() -> None:
    """The local project and pip may be outside the third-party release lock."""
    comparison = compare_environment(
        {"setuptools": ("setuptools", "83.0.0")},
        {
            "pip": ("pip", "26.1.2"),
            "setuptools": ("setuptools", "83.0.0"),
            "fenestra": ("fenestra", "1.5.0"),
        },
        ("pip", "fenestra"),
    )

    assert comparison["success"] is True
