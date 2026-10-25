"""WhatsApp-specific metadata and presentation over shared web-app state."""

from __future__ import annotations

from dataclasses import dataclass

from docking.applets.webapp.state import (
    BrowserPhase,
    NavigationTarget,
    WebAppState,
    parse_unread_count,
)
from docking.applets.webapp.state import (
    navigation_target as classify_navigation,
)
from docking.i18n import _

WHATSAPP_WEB_URL = "https://web.whatsapp.com/"
WHATSAPP_WEB_HOST = "web.whatsapp.com"

__all__ = [
    "BrowserPhase",
    "NavigationTarget",
    "WhatsAppState",
    "navigation_target",
    "parse_unread_count",
    "tooltip_text",
]


@dataclass(frozen=True, slots=True)
class WhatsAppState(WebAppState):
    """Unread metadata for the WhatsApp session."""


def navigation_target(uri: str | None) -> NavigationTarget:
    return classify_navigation(uri, WHATSAPP_WEB_HOST)


def tooltip_text(state: WebAppState) -> str:
    """Build the concise applet tooltip for the current browser state."""
    if state.phase is BrowserPhase.UNAVAILABLE:
        return _("WhatsApp: WebKitGTK is not installed")
    if state.phase is BrowserPhase.STARTING:
        return _("WhatsApp: loading...")
    if state.phase is BrowserPhase.LOGIN_REQUIRED:
        return _("WhatsApp: scan the QR code to connect")
    if state.phase is BrowserPhase.SYNCING:
        return _("WhatsApp: synchronizing...")
    if state.phase is BrowserPhase.OFFLINE:
        return _("WhatsApp: offline")
    if state.phase is BrowserPhase.ERROR:
        if state.error:
            return _("WhatsApp: {error}").format(error=state.error)
        return _("WhatsApp: could not load WhatsApp Web")
    if state.phase is BrowserPhase.READY:
        if state.badge_uses_notification_fallback:
            if state.badge_count == 1:
                return _("WhatsApp: 1 new notification")
            return _("WhatsApp: {count} new notifications").format(
                count=state.badge_count
            )
        if state.unread_count == 1:
            return _("WhatsApp: 1 unread message")
        if state.unread_count > 1:
            return _("WhatsApp: {count} unread messages").format(
                count=state.unread_count
            )
        return _("WhatsApp: no unread messages")
    return _("WhatsApp: click to open")
