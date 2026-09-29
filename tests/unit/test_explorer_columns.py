"""Windows-only regression tests for Explorer column autosizing."""

import sys
from types import SimpleNamespace

import pytest


def test_partial_column_autosize_is_not_deduplicated(monkeypatch):
    """One successful column cannot hide failures in the remaining columns."""
    comtypes_module = sys.modules.get("comtypes")
    if sys.platform != "win32" or not hasattr(comtypes_module, "GUID"):
        pytest.skip("The real Windows comtypes ABI is required.")

    from fenestra.services import explorer_columns

    shell_window = SimpleNamespace()
    monkeypatch.setattr(
        explorer_columns,
        "find_explorer_tab_by_index",
        lambda *args, **kwargs: shell_window,
    )
    monkeypatch.setattr(
        explorer_columns,
        "apply_to_window",
        lambda *args, **kwargs: (3, 1, explorer_columns.FVM_DETAILS),
    )

    result = explorer_columns.autosize_explorer_columns(
        42,
        target_index=0,
        target_path=r"C:\folder",
    )

    assert result == (False, "partial")
