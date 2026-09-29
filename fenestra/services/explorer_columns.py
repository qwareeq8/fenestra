#!/usr/bin/env python3
"""Explorer column autosizing through COM ``IColumnManager`` without fallbacks.

The implementation obtains ``IColumnManager`` through
``IServiceProvider/SID_SFolderView`` and applies ``CM_WIDTH_AUTOSIZE`` to every
visible column. Windows 11 tabs are tracked by COM ``IUnknown`` identity rather
than HWND alone, and transient navigation errors are handled without a keyboard
fallback.
"""

from __future__ import annotations

import ctypes
import logging
import os
from collections.abc import Iterable
from ctypes import c_uint, c_void_p, sizeof, wintypes
from dataclasses import dataclass, field

import comtypes
import comtypes.client
from comtypes import COMMETHOD, GUID, HRESULT, POINTER, IUnknown, byref, cast

from fenestra.platform.paths import canonicalize_path, resolve_explorer_location
from fenestra.services.explorer_constants import FVM_DETAILS

LOG = logging.getLogger("Fenestra")


class IServiceProvider(IUnknown):
    """Declare the pointer-safe COM ``IServiceProvider`` interface."""

    _iid_ = GUID("{6D5140C1-7436-11CE-8034-00AA006009FA}")
    _methods_ = [  # noqa: RUF012 - comtypes requires mutable class metadata.
        COMMETHOD(
            [],
            HRESULT,
            "QueryService",
            (["in"], POINTER(GUID), "guidService"),
            (["in"], POINTER(GUID), "riid"),
            (["out"], POINTER(c_void_p), "ppvObject"),
        ),
    ]


class PROPERTYKEY(ctypes.Structure):
    """Describe a Windows property-system key."""

    _fields_ = [
        ("fmtid", GUID),
        ("pid", wintypes.DWORD),
    ]


class CM_COLUMNINFO(ctypes.Structure):
    """Describe the native column metadata exchanged with ``IColumnManager``."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("dwMask", wintypes.DWORD),
        ("dwState", wintypes.DWORD),
        ("uWidth", wintypes.UINT),
        ("uDefaultWidth", wintypes.UINT),
        ("uIdealWidth", wintypes.UINT),
        ("wszName", wintypes.WCHAR * 80),
    ]


CM_MASK_WIDTH = 0x00000001
CM_MASK_DEFAULTWIDTH = 0x00000002
CM_MASK_IDEALWIDTH = 0x00000004
CM_MASK_NAME = 0x00000008
CM_MASK_STATE = 0x00000010

CM_ENUM_VISIBLE = 0x00000002

CM_WIDTH_AUTOSIZE = -2

FOLDERVIEWMODE_NAMES = {
    -1: "FVM_AUTO",
    1: "FVM_ICON",
    2: "FVM_SMALLICON",
    3: "FVM_LIST",
    4: "FVM_DETAILS",
    5: "FVM_THUMBNAIL",
    6: "FVM_TILE",
    7: "FVM_THUMBSTRIP",
    8: "FVM_CONTENT",
}

SID_SFolderView = GUID("{CDE725B0-CCC9-4519-917E-325D72FAB4CE}")


class IColumnManager(IUnknown):
    """Declare the COM interface used to inspect and resize Explorer columns."""

    _iid_ = GUID("{D8EC27BB-3F3B-4042-B10A-4ACFD924D453}")
    _methods_ = [  # noqa: RUF012 - comtypes requires mutable class metadata.
        COMMETHOD(
            [],
            HRESULT,
            "SetColumnInfo",
            (["in"], POINTER(PROPERTYKEY), "propkey"),
            (["in"], POINTER(CM_COLUMNINFO), "pcmci"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetColumnInfo",
            (["in"], POINTER(PROPERTYKEY), "propkey"),
            (["in", "out"], POINTER(CM_COLUMNINFO), "pcmci"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetColumnCount",
            (["in"], wintypes.DWORD, "dwFlags"),
            (["in"], POINTER(wintypes.UINT), "puCount"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "GetColumns",
            (["in"], wintypes.DWORD, "dwFlags"),
            (["in"], POINTER(PROPERTYKEY), "rgkeyOrder"),
            (["in"], wintypes.UINT, "cColumns"),
        ),
        COMMETHOD(
            [],
            HRESULT,
            "SetColumns",
            (["in"], POINTER(PROPERTYKEY), "rgkeyOrder"),
            (["in"], wintypes.UINT, "cVisible"),
        ),
    ]


def _get_com_identity(dispatch: object) -> int:
    """Return the COM ``IUnknown`` identity for a dispatch object.

    This provides a stable identity for the COM object that can be used to
    distinguish between different tabs in Windows 11 tabbed Explorer, even
    when they share the same top-level HWND.
    """
    try:
        unk = dispatch.QueryInterface(IUnknown)
        # COM identity rule: two references address the same object when their
        # IUnknown pointer values are equal. The wrapper releases its reference
        # on GC; only the raw address is kept.
        return int(cast(unk, c_void_p).value or 0)
    except Exception:
        # Fall back to the Python object identity.
        return id(dispatch)


def _get_view_mode_safe(dispatch: object) -> int | None:
    """Return the view mode, or ``None`` while the COM document is unavailable."""
    try:
        doc = getattr(dispatch, "Document", None)
        if doc is None:
            return None
        return int(doc.CurrentViewMode)
    except Exception:
        return None


@dataclass(frozen=True)
class ShellWindow:
    """Describe one ShellWindows collection entry, including its tab identity."""

    dispatch: object
    hwnd: int
    exe_name: str
    location_url: str
    tab_index: int = field(default=-1)
    tab_id: int = field(default=0)  # This is the unique COM identity used for tab tracking.
    view_mode: int | None = field(default=None)  # ``FVM_DETAILS`` is 4.


def iter_shell_windows(logger: logging.Logger) -> Iterable[ShellWindow]:
    """Yield available ShellWindows entries with per-window tab indices."""
    try:
        shell = comtypes.client.CreateObject("Shell.Application")
    except Exception as e:
        logger.debug("Creating Shell.Application failed: %r.", e)
        return

    try:
        windows = shell.Windows()
        if windows is None:
            return
    except Exception as e:
        logger.debug("Calling Shell.Application.Windows() failed: %r.", e)
        return

    try:
        count = int(windows.Count)
    except Exception as e:
        logger.debug("Reading the Shell.Application.Windows() count failed: %r.", e)
        return
    logger.debug("Shell.Application.Windows() reported %d windows.", count)
    tab_indices: dict[int, int] = {}
    for i in range(count):
        try:
            w = windows.Item(i)
            if w is None:
                continue
            hwnd = int(w.HWND)
            full_name = str(getattr(w, "FullName", "") or "")
            exe = os.path.basename(full_name).lower()
            loc = str(getattr(w, "LocationURL", "") or "")
            tab_index = tab_indices.get(hwnd, 0)
            tab_indices[hwnd] = tab_index + 1
            tab_id = _get_com_identity(w)
            view_mode = _get_view_mode_safe(w)
            yield ShellWindow(
                dispatch=w,
                hwnd=hwnd,
                exe_name=exe,
                location_url=loc,
                tab_index=tab_index,
                tab_id=tab_id,
                view_mode=view_mode,
            )
        except Exception as e:
            logger.debug("Reading ShellWindows item %d failed: %r.", i, e)
            continue


def iter_explorer_tabs(logger: logging.Logger) -> Iterable[ShellWindow]:
    """Yield every Explorer tab as a ``ShellWindow`` with a tab identity.

    This handles Windows 11 tabbed Explorer by yielding multiple entries
    for the same HWND (one per tab).
    """
    for sw in iter_shell_windows(logger):
        if sw.exe_name == "explorer.exe":
            yield sw


def find_explorer_tab_by_index(
    logger: logging.Logger,
    hwnd: int,
    tab_index: int,
    expected_path: str | None = None,
) -> ShellWindow | None:
    """Find a tab by its per-window collection index and current location.

    The per-window index distinguishes duplicate tabs that have the same HWND
    and path without changing when an unrelated shell window opens. Verifying
    the path prevents a concurrent collection change from targeting an
    unrelated tab.
    """
    expected = canonicalize_path(expected_path or "")
    for sw in iter_explorer_tabs(logger):
        if sw.hwnd != hwnd or sw.tab_index != tab_index:
            continue
        actual = canonicalize_path(resolve_explorer_location(sw.dispatch))
        if expected and actual != expected:
            logger.debug(
                "The Explorer location changed for hwnd=%s and tab index=%s.",
                hwnd,
                tab_index,
            )
            return None
        return sw
    return None


def _format_hresult(hr: int) -> str:
    return f"0x{(hr & 0xFFFFFFFF):08X}"


def _format_propertykey(pk: PROPERTYKEY) -> str:
    return f"{pk.fmtid}:{int(pk.pid)}"


def query_service_raw(
    logger: logging.Logger,
    dispatch_obj: object,
    service_guid: GUID,
    iid: GUID,
) -> c_void_p:
    """Return the raw interface pointer that ``IServiceProvider.QueryService`` yields.

    The caller owns the returned reference and must wrap it exactly once.
    """
    unk = dispatch_obj.QueryInterface(IUnknown)
    sp = unk.QueryInterface(IServiceProvider)
    ppv_obj = sp.QueryService(byref(service_guid), byref(iid))
    ptr = ppv_obj.value if hasattr(ppv_obj, "value") else int(ppv_obj)
    logger.debug(
        "QueryService result, source_type=%s, service=%s, riid=%s, ppv=%s.",
        type(dispatch_obj),
        str(service_guid),
        str(iid),
        hex(ptr) if ptr else "0x0",
    )
    if not ptr:
        raise RuntimeError("QueryService returned a null pointer.")
    return c_void_p(ptr)


def get_view_mode_from_document(window_dispatch: object) -> tuple[int | None, str, str]:
    """Return the Explorer view mode, its name, and an error from the window Document."""
    try:
        doc = getattr(window_dispatch, "Document", None)
        if doc is None:
            return None, "UNKNOWN", "Document was None."
        mode = int(doc.CurrentViewMode)
        name = FOLDERVIEWMODE_NAMES.get(mode, f"UNKNOWN({mode})")
        return mode, name, ""
    except Exception as e:
        return None, "UNKNOWN", f"Document.CurrentViewMode failed. Error was: {e!r}."


def get_service_provider_sources(window_dispatch: object) -> list[tuple[str, object]]:
    """Return the named COM objects to query for the folder view, Document first."""
    sources: list[tuple[str, object]] = []
    try:
        doc = getattr(window_dispatch, "Document", None)
        if doc is not None:
            sources.append(("document", doc))
    except Exception:
        pass
    sources.append(("window", window_dispatch))
    return sources


def _dump_visible_columns(
    logger: logging.Logger, cm: POINTER(IColumnManager), keys: ctypes.Array, n: int
) -> None:
    info = CM_COLUMNINFO()
    info.cbSize = sizeof(CM_COLUMNINFO)
    info.dwMask = (
        CM_MASK_NAME | CM_MASK_WIDTH | CM_MASK_DEFAULTWIDTH | CM_MASK_IDEALWIDTH | CM_MASK_STATE
    )
    for i in range(n):
        pk = keys[i]
        try:
            result = cm.GetColumnInfo(byref(pk), info)
            hr = int(result) if isinstance(result, int) else 0
            if hr != 0:
                logger.debug(
                    "GetColumnInfo failed for %s, hr=%s.",
                    _format_propertykey(pk),
                    _format_hresult(hr),
                )
                continue
            name = "".join(info.wszName).rstrip("\0")
            logger.debug(
                "Visible column %d/%d, key=%s, name=%r, "
                "width=%d, default_width=%d, ideal_width=%d, state=0x%08X.",
                i + 1,
                n,
                _format_propertykey(pk),
                name,
                int(info.uWidth),
                int(info.uDefaultWidth),
                int(info.uIdealWidth),
                int(info.dwState),
            )
        except Exception as e:
            logger.debug(
                "GetColumnInfo exception for %s. Error was: %r", _format_propertykey(pk), e
            )


def autosize_visible_columns_for_dispatch(
    logger: logging.Logger,
    window_dispatch: object,
    dump_columns: bool,
) -> tuple[int, int]:
    """Autosize every visible column and return the attempted and succeeded counts.

    Each service-provider source is tried in turn until one yields an
    ``IColumnManager``.
    """
    last_error: str | None = None
    for source_name, src in get_service_provider_sources(window_dispatch):
        try:
            logger.debug("Autosize start using source=%s.", source_name)
            ppv = query_service_raw(logger, src, SID_SFolderView, IColumnManager._iid_)
            cm = cast(ppv, POINTER(IColumnManager))
            try:
                count = wintypes.UINT(0)
                logger.debug("Calling IColumnManager.GetColumnCount.")
                hr = int(cm.GetColumnCount(CM_ENUM_VISIBLE, byref(count)))
                if hr != 0:
                    raise comtypes.COMError(hr, "GetColumnCount failed.", None)
                n = int(count.value)
                logger.debug("Visible column count was %d.", n)
                if n <= 0:
                    return (0, 0)
                keys = (PROPERTYKEY * n)()
                logger.debug("Calling IColumnManager.GetColumns for %d columns.", n)
                hr = int(cm.GetColumns(CM_ENUM_VISIBLE, keys, n))
                if hr != 0:
                    raise comtypes.COMError(hr, "GetColumns failed.", None)
                if dump_columns:
                    _dump_visible_columns(logger, cm, keys, n)
                info = CM_COLUMNINFO()
                info.cbSize = sizeof(CM_COLUMNINFO)
                info.dwMask = CM_MASK_WIDTH
                info.dwState = 0
                info.uWidth = c_uint(CM_WIDTH_AUTOSIZE).value
                info.uDefaultWidth = 0
                info.uIdealWidth = 0
                info.wszName = "\0" * 80
                attempted = 0
                succeeded = 0
                for i in range(n):
                    attempted += 1
                    pk = keys[i]
                    try:
                        hr = int(cm.SetColumnInfo(byref(pk), byref(info)))
                        if hr == 0:
                            succeeded += 1
                        else:
                            logger.debug(
                                "SetColumnInfo failed for %s, hr=%s.",
                                _format_propertykey(pk),
                                _format_hresult(hr),
                            )
                    except Exception as e:
                        logger.debug(
                            "SetColumnInfo exception for %s. Error was: %r",
                            _format_propertykey(pk),
                            e,
                        )
                logger.debug("Autosize finished using source=%s.", source_name)
                return (attempted, succeeded)
            finally:
                # Ownership note: QueryService returned exactly one reference,
                # and the comtypes pointer created by cast() releases it when
                # the wrapper is garbage collected. An explicit Release() here
                # would over-release the proxy by one.
                del cm
        except Exception as e:
            # Keep only the text. The exception's traceback frames hold COM
            # pointers, and a stored exception would leave them to the cyclic
            # garbage collector, which may release them on another thread.
            last_error = repr(e)
            logger.debug("Autosize failed using source=%s. Error was: %r.", source_name, e)
    if last_error is not None:
        raise RuntimeError(f"Autosize failed for every source. The last error was {last_error}.")
    raise RuntimeError("Could not acquire IColumnManager from any service provider source.")


def apply_to_window(
    logger: logging.Logger,
    sw: ShellWindow,
    require_details: bool,
    dump_columns: bool,
) -> tuple[int, int, int | None]:
    """Autosize one window's columns and return counts plus the view mode.

    The view mode is returned so callers can distinguish "skipped because the
    view is not Details" (permanent until the user changes the view) from a
    genuine failure worth retrying.
    """
    mode, name, err = get_view_mode_from_document(sw.dispatch)
    if mode is not None:
        logger.debug(
            "HWND %d, Document view %d (%s), URL %s.",
            sw.hwnd,
            mode,
            name,
            sw.location_url,
        )
    else:
        logger.debug(
            "HWND %d, Document view UNKNOWN, %s, URL %s.",
            sw.hwnd,
            err,
            sw.location_url,
        )
    if require_details and mode is not None and mode != FVM_DETAILS:
        logger.debug("Skip HWND %d, effective view was %d (%s).", sw.hwnd, mode, name)
        return (0, 0, mode)
    attempted, succeeded = autosize_visible_columns_for_dispatch(
        logger, sw.dispatch, dump_columns=dump_columns
    )
    return (attempted, succeeded, mode)


def autosize_explorer_columns(
    hwnd: int,
    target_index: int,
    target_path: str | None = None,
    require_details: bool = True,
    dump_columns: bool = False,
) -> tuple[bool, str]:
    """Autosize visible columns for one Explorer tab through COM.

    The calling thread must already own an initialized COM apartment. The
    Explorer worker initializes one for its thread and calls this function
    only from it.

    Parameters
    ----------
    hwnd : int
        The top-level Explorer window handle.
    target_index : int
        The tab's index among the ShellWindows entries of ``hwnd``.
    target_path : str, optional
        The expected tab path, used to detect concurrent navigation.
    require_details : bool, optional
        Autosize only while the tab is in Details view.
    dump_columns : bool, optional
        Log column metadata for debugging.

    Returns
    -------
    tuple of (bool, str)
        Whether autosizing succeeded, and the method or failure reason.
    """
    LOG.debug(
        "Starting Explorer column autosizing for hwnd=%s and target_path=%s.", hwnd, target_path
    )

    try:
        # Address the exact collection entry. A path cannot distinguish two
        # tabs open to the same folder.
        sw = find_explorer_tab_by_index(LOG, hwnd, target_index, target_path)
        if sw is None:
            LOG.debug(
                "autosize_explorer_columns: Explorer window not found for HWND %s path=%s",
                hwnd,
                target_path,
            )
            return False, "not-found"
        attempted, succeeded, mode = apply_to_window(
            LOG,
            sw,
            require_details=require_details,
            dump_columns=dump_columns,
        )
        if require_details and mode is not None and mode != FVM_DETAILS:
            # Not a failure to retry: the view simply is not Details.
            return False, "not-details"
        ok = attempted > 0 and succeeded == attempted
        if ok:
            return True, "com"
        if succeeded > 0:
            return False, "partial"
        return False, "none"
    except Exception as e:
        LOG.debug("Explorer column autosizing failed: %r.", e, exc_info=True)
        return False, "error"
