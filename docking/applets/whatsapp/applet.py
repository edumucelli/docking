"""WhatsApp presentation over the shared persistent web-app lifecycle."""

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from docking.applets.webapp.applet import WebAppApplet
from docking.applets.whatsapp import meta
from docking.applets.whatsapp.render import render_icon
from docking.applets.whatsapp.state import (
    PROFILE,
    WHATSAPP_WEB_URL,
    WhatsAppState,
    tooltip_text,
)
from docking.i18n import _


class WhatsAppApplet(WebAppApplet):
    id = meta.id
    name = _("WhatsApp")
    icon_name = "phone-symbolic"
    profile = PROFILE
    state_factory = WhatsAppState
    web_url = WHATSAPP_WEB_URL
    open_label = _("Open WhatsApp")
    hide_label = _("Hide WhatsApp")
    disconnect_label = _("Disconnect WhatsApp")
    disconnect_question = _("Disconnect WhatsApp?")
    disconnect_description = _(
        "This clears the WhatsApp Web session stored by Docking. You "
        "will need to scan the QR code again."
    )
    disconnect_error = _("Could not disconnect WhatsApp")

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, phase=self._state.phase)

    def _tooltip_text(self) -> str:
        return tooltip_text(self._state)
