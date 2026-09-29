"""Pure URL policy helpers for Fenestra's privileged WebChannel page."""

from __future__ import annotations

from urllib.parse import unquote, urlsplit


def _file_identity(url: str) -> tuple[str, str] | None:
    parsed = urlsplit(url)
    if parsed.scheme.casefold() != "file":
        return None
    host = parsed.netloc.casefold()
    path = unquote(parsed.path).replace("\\", "/").casefold()
    return host, path.rstrip("/")


def _origin(url: str) -> tuple[str, str, int] | None:
    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return None
    default_port = 443 if scheme == "https" else 80
    return scheme, parsed.hostname.casefold(), parsed.port or default_port


def is_trusted_document_navigation(
    candidate_url: str,
    *,
    frontend_url: str | None,
    dev_server_url: str,
    dev_mode: bool,
    allow_error_document: bool = False,
) -> bool:
    """Return whether a main-frame navigation may retain bridge access."""
    parsed = urlsplit(candidate_url)
    scheme = parsed.scheme.casefold()

    if allow_error_document and scheme in {"about", "data"}:
        return True
    if dev_mode:
        trusted_origin = _origin(dev_server_url)
        return trusted_origin is not None and _origin(candidate_url) == trusted_origin
    if frontend_url is None:
        return False
    trusted_file = _file_identity(frontend_url)
    return trusted_file is not None and _file_identity(candidate_url) == trusted_file
