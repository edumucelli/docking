"""Pure state and navigation policy for the WhatsApp applet."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from urllib.parse import urlparse

from docking.i18n import _

WHATSAPP_WEB_URL = "https://web.whatsapp.com/"
WHATSAPP_WEB_HOST = "web.whatsapp.com"

_UNREAD_TITLE_RE = re.compile(r"^\s*\((\d+)(?:\+)?\)")
_INTERNAL_SCHEMES = frozenset({"about", "blob", "data"})
_EXTERNAL_SCHEMES = frozenset({"http", "https", "mailto", "tel"})


class BrowserPhase(str, Enum):
    """High-level WebKit lifecycle visible to the applet."""

    STOPPED = "stopped"
    STARTING = "starting"
    LOGIN_REQUIRED = "login_required"
    SYNCING = "syncing"
    READY = "ready"
    OFFLINE = "offline"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


class NavigationTarget(str, Enum):
    """How a top-level navigation request should be handled."""

    INTERNAL = "internal"
    EXTERNAL = "external"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class WhatsAppState:
    """Small immutable presentation state owned by the applet."""

    phase: BrowserPhase = BrowserPhase.STOPPED
    title_unread_count: int = 0
    api_unread_count: int | None = None
    notification_count: int = 0
    title: str = ""
    error: str = ""

    @property
    def unread_count(self) -> int:
        """Return the best exact unread count currently available."""
        if self.api_unread_count is not None:
            return self.api_unread_count
        return self.title_unread_count

    @property
    def badge_count(self) -> int:
        """Return exact unread state or the notification fallback count."""
        if self.api_unread_count is not None:
            return self.api_unread_count
        if self.title_unread_count > 0:
            return self.title_unread_count
        return self.notification_count

    @property
    def badge_uses_notification_fallback(self) -> bool:
        return (
            self.api_unread_count is None
            and self.title_unread_count <= 0
            and self.notification_count > 0
        )

    def with_title(self, title: str | None) -> WhatsAppState:
        clean_title = (title or "").strip()
        return replace(
            self,
            title=clean_title,
            title_unread_count=parse_unread_count(clean_title),
        )

    def with_api_badge(self, count: int | None, *, visible: bool) -> WhatsAppState:
        """Apply one standard Badging API update from WhatsApp Web."""
        if count is None:
            if visible:
                return self.with_notification()
            return self
        return replace(self, api_unread_count=max(0, count))

    def with_notification(self) -> WhatsAppState:
        """Record new activity when no exact unread source is available."""
        api_unread_count = None if self.api_unread_count == 0 else self.api_unread_count
        return replace(
            self,
            api_unread_count=api_unread_count,
            notification_count=min(99, self.notification_count + 1),
        )

    def clear_notification_fallback(self) -> WhatsAppState:
        if self.notification_count == 0:
            return self
        return replace(self, notification_count=0)

    def reset_page_badge(self) -> WhatsAppState:
        """Discard page-scoped badge signals while a new document loads."""
        return replace(
            self,
            title="",
            title_unread_count=0,
            api_unread_count=None,
        )


def parse_unread_count(title: str | None) -> int:
    """Extract WhatsApp's leading ``(N)`` or ``(N+)`` unread marker.

    Only the leading marker is accepted. Chat names and other title text may
    contain numbers, so collecting every digit from the title would produce
    incorrect badge counts.
    """
    if not title:
        return 0
    match = _UNREAD_TITLE_RE.match(title)
    if match is None:
        return 0
    try:
        return max(0, int(match.group(1)))
    except ValueError:
        return 0


def navigation_target(uri: str | None) -> NavigationTarget:
    """Classify one requested navigation without performing any I/O."""
    if not uri:
        return NavigationTarget.BLOCKED
    parsed = urlparse(uri)
    scheme = parsed.scheme.casefold()
    if scheme in _INTERNAL_SCHEMES:
        return NavigationTarget.INTERNAL
    if scheme not in _EXTERNAL_SCHEMES:
        return NavigationTarget.BLOCKED
    if scheme == "https" and (parsed.hostname or "").casefold() == WHATSAPP_WEB_HOST:
        return NavigationTarget.INTERNAL
    return NavigationTarget.EXTERNAL


def tooltip_text(state: WhatsAppState) -> str:
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
