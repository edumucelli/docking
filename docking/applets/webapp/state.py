"""Shared unread state and navigation policy for embedded web apps."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum
from urllib.parse import urlparse

_UNREAD_TITLE_RE = re.compile(r"^\s*\((\d+)(?:\+)?\)")
_INTERNAL_SCHEMES = frozenset({"about", "blob", "data"})
_EXTERNAL_SCHEMES = frozenset({"http", "https", "mailto", "tel"})


class BrowserPhase(str, Enum):
    """High-level browser lifecycle visible to the applet."""

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
class WebAppState:
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

    def with_title(self, title: str | None) -> WebAppState:
        clean_title = (title or "").strip()
        return replace(
            self,
            title=clean_title,
            title_unread_count=parse_unread_count(clean_title),
        )

    def with_api_badge(self, count: int | None, *, visible: bool) -> WebAppState:
        """Apply one standard browser Badging API update."""
        if count is None:
            if visible:
                return self.with_notification()
            return self
        return replace(self, api_unread_count=max(0, count))

    def with_notification(self) -> WebAppState:
        """Record new activity when no exact unread source is available."""
        api_unread_count = None if self.api_unread_count == 0 else self.api_unread_count
        return replace(
            self,
            api_unread_count=api_unread_count,
            notification_count=min(99, self.notification_count + 1),
        )

    def clear_notification_fallback(self) -> WebAppState:
        if self.notification_count == 0:
            return self
        return replace(self, notification_count=0)

    def reset_page_badge(self) -> WebAppState:
        """Discard page-scoped badge signals while a new document loads."""
        return replace(
            self,
            title="",
            title_unread_count=0,
            api_unread_count=None,
        )


def parse_unread_count(title: str | None) -> int:
    """Extract web app's leading ``(N)`` or ``(N+)`` unread marker.

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


def navigation_target(uri: str | None, web_host: str) -> NavigationTarget:
    """Classify one requested navigation without performing any I/O."""
    if not uri:
        return NavigationTarget.BLOCKED
    try:
        parsed = urlparse(uri)
        port = parsed.port
    except ValueError:
        return NavigationTarget.BLOCKED
    scheme = parsed.scheme.casefold()
    if scheme in _INTERNAL_SCHEMES:
        return NavigationTarget.INTERNAL
    if scheme not in _EXTERNAL_SCHEMES:
        return NavigationTarget.BLOCKED
    if (
        scheme == "https"
        and (parsed.hostname or "").casefold() == web_host
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
    ):
        return NavigationTarget.INTERNAL
    return NavigationTarget.EXTERNAL
