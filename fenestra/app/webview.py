"""QWebEngineView host for the Fenestra React frontend.

``FenestraWebView`` fills its parent, registers ``FenestraBridge`` as ``bridge``
through ``QWebChannel``, and routes JavaScript console messages to Python
logging. Development mode connects to the Vite server, while release mode
loads static files from ``frontend/dist``.

Usage in ``MainWindow``::

    self.webview = FenestraWebView(bridge, parent=self)
    # Add self.webview to the central widget layout.
"""

import logging
import os

from PySide6.QtCore import QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from fenestra.bridge import FenestraBridge
from fenestra.platform.resources import resource_path
from fenestra.platform.web_navigation import is_trusted_document_navigation

LOG = logging.getLogger("Fenestra")

# Development mode requires ``--dev`` or ``FENESTRA_DEV=1``; see _is_dev_mode.
DEV_SERVER_URL = "http://localhost:5173"

_MISSING_FRONTEND_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Fenestra</title>
<style>
  body { font-family: system-ui, sans-serif; background: #0e0f12; color: #e6e8ec;
         display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
  .box { max-width: 480px; text-align: center; }
  h1 { font-size: 20px; font-weight: 600; margin-bottom: 12px; }
  p { font-size: 14px; color: #9a9ea8; line-height: 1.6; }
  code { background: #1c1f26; padding: 2px 6px; border-radius: 3px; font-size: 13px; }
</style>
</head>
<body>
<div class="box">
  <h1>Frontend build not found</h1>
  <p>The file <code>frontend/dist/index.html</code> is missing.<br>
  Run <code>scripts/build-frontend.ps1</code> to build the frontend.</p>
</div>
</body>
</html>"""


def _is_dev_mode() -> bool:
    """Return whether to load the frontend from the Vite development server.

    Development mode requires an explicit ``--dev`` flag or ``FENESTRA_DEV=1``.
    Running from source without either behaves like a release build.
    """
    return os.environ.get("FENESTRA_DEV", "").lower() in ("1", "true", "yes")


def _get_frontend_url() -> QUrl | None:
    """Return the URL for the React frontend, or None if missing in release mode."""
    if _is_dev_mode():
        LOG.info("WebView: development mode is loading from %s.", DEV_SERVER_URL)
        return QUrl(DEV_SERVER_URL)

    # Release mode loads ``frontend/dist/index.html`` through a file URL.
    dist_path = resource_path(os.path.join("frontend", "dist", "index.html"))
    if not os.path.exists(dist_path):
        LOG.error("WebView: the frontend build is missing at %s.", dist_path)
        return None
    LOG.info("WebView: release mode is loading from %s.", dist_path)
    return QUrl.fromLocalFile(dist_path)


class FenestraWebPage(QWebEnginePage):
    """Custom page that routes JS console to Python logging and filters navigation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frontend_url: str | None = None
        self._dev_mode = False
        self._allow_error_document = False

    def set_navigation_policy(self, frontend_url: QUrl | None, dev_mode: bool) -> None:
        """Pin privileged document navigation to Fenestra's selected frontend."""
        self._frontend_url = frontend_url.toString() if frontend_url is not None else None
        self._dev_mode = dev_mode
        self._allow_error_document = frontend_url is None

    def clear_error_document_allowance(self, *_args) -> None:
        """Expire the one-time fallback-document navigation allowance."""
        self._allow_error_document = False

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        """Allow only the selected main document to retain bridge access."""
        if not is_main_frame:
            LOG.warning("Blocked subframe navigation to %s.", url.toString())
            return False
        allowed = is_trusted_document_navigation(
            url.toString(),
            frontend_url=self._frontend_url,
            dev_server_url=DEV_SERVER_URL,
            dev_mode=self._dev_mode,
            allow_error_document=self._allow_error_document,
        )
        if allowed:
            if url.scheme().lower() in {"about", "data"}:
                self._allow_error_document = False
            return True
        LOG.warning(
            "Blocked navigation to %s (type=%s, main_frame=%s).",
            url.toString(),
            nav_type,
            is_main_frame,
        )
        return False

    def javaScriptConsoleMessage(self, level, message, line, source):
        """Forward frontend console output to the application log."""
        prefix = f"[JS:{source}:{line}]"
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            LOG.error("%s %s", prefix, message)
        elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel:
            LOG.warning("%s %s", prefix, message)
        else:
            LOG.debug("%s %s", prefix, message)


class FenestraWebView(QWebEngineView):
    """QWebEngineView hosting the Fenestra React frontend.

    A ``QWebChannel`` registers the provided ``FenestraBridge`` as ``bridge``.
    Development mode uses the Vite server, while release mode uses local files.

    The public ``bridge`` attribute exposes the ``FenestraBridge`` instance.
    """

    def __init__(self, bridge: FenestraBridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge

        frontend_url = _get_frontend_url()
        dev_mode = _is_dev_mode()

        # Use a custom page for JavaScript console routing.
        page = FenestraWebPage(self)
        page.set_navigation_policy(frontend_url, dev_mode)
        page.loadFinished.connect(page.clear_error_document_allowance)

        # JavaScript and local file access are on by default. Local content
        # reaches remote URLs only in development mode.
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, dev_mode
        )

        # Set up ``QWebChannel``.
        self._channel = QWebChannel(page)
        self._channel.registerObject("bridge", bridge)
        page.setWebChannel(self._channel)

        self.setPage(page)

        # Disable the context menu in release mode to prevent inspector access.
        if not dev_mode:
            from PySide6.QtCore import Qt

            self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        # Load the frontend.
        if frontend_url is None:
            page.setHtml(_MISSING_FRONTEND_HTML)
            LOG.warning("FenestraWebView: showing the missing-frontend error page.")
        else:
            self.setUrl(frontend_url)
            LOG.info("FenestraWebView initialized and is loading %s.", frontend_url.toString())
