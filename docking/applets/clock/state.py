"""Pure state helpers for Clock applet."""

from __future__ import annotations

import math
import time
from typing import Any, Mapping

# Default preferences
DEFAULT_PREFS: dict[str, Any] = {
    "show_digital": False,
    "show_military": False,
    "show_date": False,
}


def minute_rotation(minute: int) -> float:
    """Rotation angle (radians) for the minute hand."""
    return math.pi * (minute / 30.0 + 1.0)


def hour_rotation_12h(hour: int, minute: int) -> float:
    """Rotation angle (radians) for the hour hand in 12-hour mode."""
    return math.pi * (hour % 12 / 6.0 + minute / 360.0 + 1.0)


def hour_rotation_24h(hour: int, minute: int) -> float:
    """Rotation angle (radians) for the hour hand in 24-hour mode."""
    return math.pi * (hour % 24 / 12.0 + minute / 720.0 + 1.0)


def load_prefs(prefs: Mapping[str, Any] | None) -> tuple[bool, bool, bool]:
    """Load display prefs from persisted applet prefs mapping."""
    if not prefs:
        return (
            bool(DEFAULT_PREFS["show_digital"]),
            bool(DEFAULT_PREFS["show_military"]),
            bool(DEFAULT_PREFS["show_date"]),
        )
    return (
        bool(prefs.get("show_digital", DEFAULT_PREFS["show_digital"])),
        bool(prefs.get("show_military", DEFAULT_PREFS["show_military"])),
        bool(prefs.get("show_date", DEFAULT_PREFS["show_date"])),
    )


def build_tooltip(now: time.struct_time, is_24h: bool) -> str:
    """Build tooltip date/time string for current mode."""
    if is_24h:
        return time.strftime("%a, %b %-d %H:%M", now)
    return time.strftime("%a, %b %-d %-I:%M %p", now)


def save_payload(
    show_digital: bool, show_military: bool, show_date: bool
) -> dict[str, bool]:
    """Return preferences payload to persist."""
    return {
        "show_digital": show_digital,
        "show_military": show_military,
        "show_date": show_date,
    }
