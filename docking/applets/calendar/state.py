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
