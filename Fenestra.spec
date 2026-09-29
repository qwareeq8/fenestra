# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the windowed Fenestra x64 bundle."""

import re
from pathlib import Path

from PyInstaller.utils.win32 import versioninfo

# Parse APP_VERSION with a regular expression instead of importing Fenestra modules.
# Importing Fenestra in the spec context may trigger the PySide6 import chain.
_cfg = Path("fenestra/app/config.py").read_text()
_match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', _cfg)
APP_VERSION = _match.group(1) if _match else "0.0.0"


def _windows_version_tuple(version):
    """Convert an application version into four Windows WORD components."""
    parts = version.split(".")
    if not 1 <= len(parts) <= 4 or any(not part.isdecimal() for part in parts):
        raise ValueError(f"APP_VERSION must contain one to four numeric components: {version!r}.")
    values = [int(part) for part in parts]
    if any(value > 0xFFFF for value in values):
        raise ValueError(f"APP_VERSION components must not exceed 65535: {version!r}.")
    return tuple(values + [0] * (4 - len(values)))


WINDOWS_VERSION = _windows_version_tuple(APP_VERSION)
VERSION_RESOURCE = versioninfo.VSVersionInfo(
    ffi=versioninfo.FixedFileInfo(filevers=WINDOWS_VERSION, prodvers=WINDOWS_VERSION),
    kids=[
        versioninfo.StringFileInfo(
            [
                versioninfo.StringTable(
                    "040904B0",
                    [
                        versioninfo.StringStruct("CompanyName", "Yusuf Qwareeq"),
                        versioninfo.StringStruct("FileDescription", "Fenestra"),
                        versioninfo.StringStruct("FileVersion", APP_VERSION),
                        versioninfo.StringStruct("InternalName", "Fenestra"),
                        versioninfo.StringStruct(
                            "LegalCopyright", "Copyright (c) 2024 Yusuf Qwareeq"
                        ),
                        versioninfo.StringStruct("OriginalFilename", "Fenestra.exe"),
                        versioninfo.StringStruct("ProductName", "Fenestra"),
                        versioninfo.StringStruct("ProductVersion", APP_VERSION),
                    ],
                )
            ]
        ),
        versioninfo.VarFileInfo([versioninfo.VarStruct("Translation", [1033, 1200])]),
    ],
)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("icon.ico", "."),
        ("LICENSE", "."),
        ("frontend/dist", "frontend/dist"),
    ],
    hiddenimports=[
        "fenestra",
        "fenestra.app",
        "fenestra.app.window",
        "fenestra.app.config",
        "fenestra.app.webview",
        "fenestra.app.__main__",
        "fenestra.bridge",
        "fenestra.bridge.bridge",
        "fenestra.bridge.capture_guard",
        "fenestra.services",
        "fenestra.services.snap",
        "fenestra.platform.theme",
        "fenestra.platform.startup",
        "fenestra.services.explorer_columns",
        "fenestra.settings",
        "fenestra.settings.persistence",
        "fenestra.settings.state",
        "fenestra.workers",
        "fenestra.workers.key_capture",
        "fenestra.workers.explorer",
        "fenestra.platform",
        "fenestra.platform.win32_helpers",
        "fenestra.platform.resources",
        "fenestra.platform.paths",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebChannel",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The delayed win32com makepy graph pulls in Pythonwin's MFC UI even though
    # Fenestra uses only Dispatch and never imports pywin or win32ui.
    excludes=["pywin", "win32ui"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Fenestra",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    # PyInstaller does not cross-compile Windows executables. The active native
    # Python interpreter and its bootloader determine the output architecture.
    target_arch=None,
    version=VERSION_RESOURCE,
    codesign_identity=None,
    entitlements_file=None,
    icon=["icon.ico"],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Fenestra",
)
