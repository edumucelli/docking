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

"""Pure state/date helpers for Calendar applet."""

from __future__ import annotations

import time
from typing import NamedTuple


class CalendarSnapshot(NamedTuple):
    """Current date/time values needed by calendar applet."""

    day: int
    weekday: str
    tooltip: str


def snapshot_from(now: time.struct_time | None = None) -> CalendarSnapshot:
    """Build applet snapshot from local time."""
    current = now or time.localtime()
    return CalendarSnapshot(
        day=current.tm_mday,
        weekday=time.strftime("%a", current),
        tooltip=time.strftime("%a, %b %-d %H:%M", current),
    )
