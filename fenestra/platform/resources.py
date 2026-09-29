"""Locate bundled resources in source checkouts and PyInstaller builds."""

import os
import sys


def resource_path(relative_path: str) -> str:
    """Return an absolute resource path for development and PyInstaller builds."""
    base_path = getattr(
        sys,
        "_MEIPASS",
        os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    )
    return os.path.join(base_path, relative_path)
