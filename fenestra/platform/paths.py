"""Explorer location resolution and path canonicalization."""

import urllib.parse


def resolve_explorer_location(window: object) -> str:
    """Return the best location string exposed by an Explorer COM window."""
    try:
        location_url = getattr(window, "LocationURL", None)
        if isinstance(location_url, str) and location_url.strip():
            return location_url.strip()
    except Exception:
        pass
    try:
        document = getattr(window, "Document", None)
        if document is not None:
            folder_path = str(document.Folder.Self.Path or "").strip()
            if folder_path:
                return folder_path
    except Exception:
        pass
    try:
        location_name = getattr(window, "LocationName", None)
        if isinstance(location_name, str):
            return location_name.strip()
    except Exception:
        pass
    return ""


def canonicalize_path(path: str) -> str:
    """Normalize an Explorer location for reliable path comparison.

    Explorer exposes local folders as ``file:///C:/...`` URLs and UNC
    folders as ``file://server/share/...`` URLs. Treating both forms as a
    string prefix removal loses the leading UNC separators, so URL parsing is
    deliberately centralized here for every Explorer caller.
    """
    if not path:
        return ""
    path = path.strip()
    if path.casefold().startswith("file:"):
        parsed = urllib.parse.urlsplit(path)
        decoded_path = urllib.parse.unquote(parsed.path)
        if parsed.netloc and parsed.netloc.casefold() != "localhost":
            path = rf"\\{parsed.netloc}{decoded_path}"
        else:
            # RFC file URLs put a slash before a Windows drive letter.
            if len(decoded_path) >= 3 and decoded_path[0] == "/" and decoded_path[2] == ":":
                decoded_path = decoded_path[1:]
            path = decoded_path
    path = path.replace("/", "\\")
    while len(path) > 3 and path.endswith("\\"):
        path = path[:-1]
    return path.casefold()
