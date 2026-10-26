"""Select one platform host behind the shared persistent browser contract."""

from __future__ import annotations

import sys
from functools import cache

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib as GLib

from .backend import BrowserProfile as BrowserProfile
from .backend import WebKitUnavailableError as WebKitUnavailableError
from .storage import _error_message as _error_message
from .storage import _session_directories as _session_directories
from .storage import _unique_download_path as _unique_download_path

if sys.platform == "win32":
    from .windows import WindowsWebAppBrowser as WebAppBrowser

    @cache
    def load_webkit():
        raise WebKitUnavailableError("WebKitGTK is unavailable on Windows")

    def webkit_available() -> bool:
        return False
else:
    from .linux import LinuxWebAppBrowser as WebAppBrowser
    from .linux import load_webkit as load_webkit
    from .linux import webkit_available as webkit_available

__all__ = [
    "BrowserProfile",
    "GLib",
    "WebAppBrowser",
    "WebKitUnavailableError",
    "_error_message",
    "_session_directories",
    "_unique_download_path",
    "load_webkit",
    "webkit_available",
]
