"""Tests for the privileged WebChannel document-navigation allowlist."""

from fenestra.platform.web_navigation import is_trusted_document_navigation


def _allowed(candidate: str, *, frontend: str | None, dev: bool = False, error=False) -> bool:
    return is_trusted_document_navigation(
        candidate,
        frontend_url=frontend,
        dev_server_url="http://localhost:5173",
        dev_mode=dev,
        allow_error_document=error,
    )


def test_release_navigation_is_pinned_to_exact_frontend_file() -> None:
    """Sibling files, remote pages, and generic data documents are rejected."""
    frontend = "file:///C:/Program%20Files/Fenestra/frontend/dist/index.html"

    assert _allowed(f"{frontend}?cache=1#snap", frontend=frontend)
    assert not _allowed(
        "file:///C:/Program%20Files/Fenestra/frontend/dist/other.html",
        frontend=frontend,
    )
    assert not _allowed("https://example.com/", frontend=frontend)
    assert not _allowed("data:text/html,untrusted", frontend=frontend)


def test_dev_navigation_requires_the_exact_vite_origin() -> None:
    """Scheme, host, and port all participate in the trusted dev origin."""
    assert _allowed("http://localhost:5173/settings?x=1", frontend=None, dev=True)
    assert not _allowed("https://localhost:5173/", frontend=None, dev=True)
    assert not _allowed("http://127.0.0.1:5173/", frontend=None, dev=True)
    assert not _allowed("http://localhost:5174/", frontend=None, dev=True)


def test_missing_frontend_error_document_requires_explicit_one_time_allowance() -> None:
    """Fallback about/data documents are never generally trusted."""
    assert not _allowed("about:blank", frontend=None)
    assert not _allowed("data:text/html,missing", frontend=None)
    assert _allowed("about:blank", frontend=None, error=True)
    assert _allowed("data:text/html,missing", frontend=None, error=True)
