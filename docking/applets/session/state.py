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

"""Shared presentation state for the Session applet."""

from __future__ import annotations

from typing import NamedTuple

from docking.i18n import _


class SessionAction(NamedTuple):
    """A semantic session or power action with its display label."""

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
