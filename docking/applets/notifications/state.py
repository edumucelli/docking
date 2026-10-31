# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""State and backend helpers for the Notifications applet."""

from __future__ import annotations

import json
from dataclasses import dataclass

from docking.applets.notifications import meta
from docking.i18n import _
from docking.log import get_logger, with_context

log = with_context(
    get_logger(name="notifications"),
    applet_id=meta.id,
)

_GNOME_SCHEMA = "org.gnome.desktop.notifications"
_GNOME_SHOW_BANNERS_KEY = "show-banners"


@dataclass(frozen=True, slots=True)
class NotificationsState:
    """Current notification backend state."""

    available: bool = False
    backend: str = ""
    paused: bool = False
    pending: int = 0
    pending_known: bool = False


def unavailable_state() -> NotificationsState:
    """Canonical unavailable state."""
    return NotificationsState()


def tooltip_text(state: NotificationsState) -> str:
    """Tooltip text for notifications applet."""
    if not state.available:
        return _("Notifications: No backend available")

    lines: list[str] = []
    if state.pending_known:
        lines.append(_("Pending: {n}").format(n=state.pending))
    if not lines:
        lines.append(_("Notifications"))
    return "\n".join(lines)


def pending_badge_count(state: NotificationsState) -> int:
    """Badge count shown on icon."""
    if not state.available or not state.pending_known:
        return 0
    return max(0, min(99, state.pending))


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"true", "1", "yes", "on"}:
        return True
    if lowered in {"false", "0", "no", "off"}:
        return False
    return None


def _parse_pending_count(value: str | None) -> int | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None

    if stripped.isdigit():
        return int(stripped)

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        log.debug("Failed to parse pending notification count %r: %s", stripped, exc)
        return None

    if isinstance(payload, dict):
        waiting = payload.get("waiting")
        if isinstance(waiting, int):
            return max(0, waiting)
    return None
