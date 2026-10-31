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

"""Pure notification status, history and presentation values."""

from __future__ import annotations

from dataclasses import dataclass

from docking.i18n import _

HISTORY_LIMIT = 40


@dataclass(frozen=True, slots=True)
class NotificationsState:
    """Current notification backend state."""

    available: bool = False
    backend: str = ""
    paused: bool = False
    pending: int = 0
    pending_known: bool = False
    paused_known: bool = False
    can_pause: bool = False
    can_clear: bool = False
    access_required: bool = False
    revision: str = ""


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


@dataclass(frozen=True, slots=True)
class NotificationEntry:
    app_name: str
    summary: str
    body: str


@dataclass(frozen=True, slots=True)
class NotificationsSnapshot:
    state: NotificationsState = NotificationsState()
    events: tuple[NotificationEntry | None, ...] = ()
