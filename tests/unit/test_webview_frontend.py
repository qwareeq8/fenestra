"""Tests for the web view's frontend location."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_qt


def test_release_frontend_url_points_at_the_bundled_build(monkeypatch, tmp_path):
    """Release mode loads frontend/dist/index.html from the resource root."""
    from fenestra.app import webview

    index = tmp_path / "frontend" / "dist" / "index.html"
    index.parent.mkdir(parents=True)
    index.write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.delenv("FENESTRA_DEV", raising=False)
    monkeypatch.setattr(webview, "resource_path", lambda relative: str(tmp_path / relative))

    url = webview._get_frontend_url()

    assert url is not None
    assert Path(url.toLocalFile()) == index


def test_missing_release_frontend_selects_the_error_page(monkeypatch, tmp_path):
    """Without a build there is no other location to try."""
    from fenestra.app import webview

    monkeypatch.delenv("FENESTRA_DEV", raising=False)
    monkeypatch.setattr(webview, "resource_path", lambda relative: str(tmp_path / relative))

    assert webview._get_frontend_url() is None
