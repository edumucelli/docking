"""Telegram presentation over the shared persistent web-app lifecycle."""

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from docking.applets.telegram import meta
from docking.applets.telegram.render import render_icon
from docking.applets.telegram.state import (
    PROFILE,
    TELEGRAM_WEB_URL,
    TelegramState,
    tooltip_text,
)
from docking.applets.webapp.applet import WebAppApplet
from docking.i18n import _


class TelegramApplet(WebAppApplet):
    id = meta.id
    name = _("Telegram")
    icon_name = "telegram"
    profile = PROFILE
    state_factory = TelegramState
    web_url = TELEGRAM_WEB_URL
    open_label = _("Open Telegram")
    hide_label = _("Hide Telegram")
    disconnect_label = _("Disconnect Telegram")
    disconnect_question = _("Disconnect Telegram?")
    disconnect_description = _(
        "This clears the Telegram Web session stored by Docking. "
        "You will need to sign in again."
    )
    disconnect_error = _("Could not disconnect Telegram")

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, phase=self._state.phase)

    def _tooltip_text(self) -> str:
        return tooltip_text(self._state)
