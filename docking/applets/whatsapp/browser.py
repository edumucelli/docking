"""WhatsApp profile for the shared persistent WebKit host."""

from docking.applets.webapp import (
    BrowserProfile,
    WebAppBrowser,
    WebKitUnavailableError,
    _error_message,
    _session_directories,
    _unique_download_path,
    load_webkit,
    webkit_available,
)
from docking.applets.webapp import (
    GLib as GLib,
)
from docking.applets.whatsapp.bridge import BRIDGE_HANDLER, BRIDGE_SCRIPT
from docking.applets.whatsapp.state import WHATSAPP_WEB_URL
from docking.platform import targets

__all__ = [
    "WebKitUnavailableError",
    "WhatsAppBrowser",
    "_error_message",
    "_session_directories",
    "_unique_download_path",
    "load_webkit",
    "webkit_available",
]

PROFILE = BrowserProfile(
    "WhatsApp", "whatsapp", WHATSAPP_WEB_URL, BRIDGE_HANDLER, BRIDGE_SCRIPT
)


class WhatsAppBrowser(WebAppBrowser):
    def __init__(self, **callbacks) -> None:
        super().__init__(profile=PROFILE, **callbacks)

    @staticmethod
    def _open_external_uri(uri: str) -> None:
        _open_external_uri(uri)


def _open_external_uri(uri: str) -> None:
    targets.open_target(uri)
