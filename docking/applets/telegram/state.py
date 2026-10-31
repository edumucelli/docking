"""Telegram's public title metadata and dock presentation."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from docking.applets.webapp.state import BrowserPhase, NavigationTarget, WebAppState
from docking.applets.webapp.state import navigation_target as classify_navigation
from docking.i18n import _, ngettext

TELEGRAM_WEB_URL = "https://web.telegram.org/k/"
TELEGRAM_WEB_HOST = "web.telegram.org"
_TITLE_COUNT = re.compile(
    r"^\s*(?:\((\d+)\+?\)|Telegram(?:\s+Web)?\s*\((\d+)\+?\))", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class TelegramState(WebAppState):
    def with_title(self, title: str | None) -> TelegramState:
        clean = (title or "").strip()
        return replace(self, title=clean, title_unread_count=parse_unread_count(clean))


def parse_unread_count(title: str | None) -> int:
    match = _TITLE_COUNT.match(title or "")
    if match is None:
        return 0
    try:
        return int(match.group(1) or match.group(2))
    except ValueError:
        return 0


def navigation_target(uri: str | None) -> NavigationTarget:
    return classify_navigation(uri, TELEGRAM_WEB_HOST)


def tooltip_text(state: WebAppState) -> str:
    if state.phase is BrowserPhase.UNAVAILABLE:
        if state.error:
            return _("Telegram: {error}").format(error=state.error)
        return _("Telegram: browser is unavailable")
    if state.phase is BrowserPhase.STARTING:
        return _("Telegram: loading...")
    if state.phase is BrowserPhase.LOGIN_REQUIRED:
        return _("Telegram: scan the QR code or sign in with your phone number")
    if state.phase is BrowserPhase.SYNCING:
        return _("Telegram: synchronizing...")
    if state.phase is BrowserPhase.OFFLINE:
        return _("Telegram: offline")
    if state.phase is BrowserPhase.ERROR:
        return _("Telegram: {error}").format(
            error=state.error or _("could not load Telegram Web")
        )
    if state.phase is BrowserPhase.READY:
        if state.badge_uses_notification_fallback:
            return ngettext(
                "Telegram: {count} new notification",
                "Telegram: {count} new notifications",
                state.badge_count,
            ).format(count=state.badge_count)
        if state.unread_count:
            return ngettext(
                "Telegram: {count} unread chat",
                "Telegram: {count} unread chats",
                state.unread_count,
            ).format(count=state.unread_count)
        return _("Telegram: no unread chats")
    return _("Telegram: click to open")
