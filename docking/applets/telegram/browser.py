"""Telegram's isolated profile for the shared WebKit host."""

from docking.applets.telegram.bridge import BRIDGE_HANDLER, BRIDGE_SCRIPT
from docking.applets.telegram.state import TELEGRAM_WEB_URL
from docking.applets.webapp import BrowserProfile, WebAppBrowser

PROFILE = BrowserProfile(
    name="Telegram",
    session_name="telegram",
    url=TELEGRAM_WEB_URL,
    bridge_handler=BRIDGE_HANDLER,
    bridge_script=BRIDGE_SCRIPT,
)


class TelegramBrowser(WebAppBrowser):
    def __init__(self, **callbacks) -> None:
        super().__init__(profile=PROFILE, **callbacks)
