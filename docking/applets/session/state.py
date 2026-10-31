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

"""State and command helpers for session applet."""

from __future__ import annotations

from typing import NamedTuple

from docking.applets.session import meta
from docking.i18n import _
from docking.log import get_logger, with_context

log = with_context(get_logger(name="session"), applet_id=meta.id)


class SessionAction(NamedTuple):
    """A session/power action with its shell command."""

    label: str
    action: str


LOCK_SCREEN_LABEL = _("Lock Screen")
_ACTIONS: list[SessionAction] = [
    SessionAction(LOCK_SCREEN_LABEL, "lock"),
    SessionAction(_("Log Out"), "logout"),
    SessionAction(_("Suspend"), "suspend"),
    SessionAction(_("Restart"), "reboot"),
    SessionAction(_("Shut Down"), "poweroff"),
]

_LOCK_SCREEN_DBUS_CANDIDATES: tuple[tuple[str, str, str], ...] = (
    (
        "org.mate.ScreenSaver",
        "/org/mate/ScreenSaver",
        "org.mate.ScreenSaver.Lock",
    ),
    (
        "org.gnome.ScreenSaver",
        "/org/gnome/ScreenSaver",
        "org.gnome.ScreenSaver.Lock",
    ),
    (
        "org.freedesktop.ScreenSaver",
        "/org/freedesktop/ScreenSaver",
        "org.freedesktop.ScreenSaver.Lock",
    ),
)
