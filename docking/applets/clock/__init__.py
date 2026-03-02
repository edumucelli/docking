"""Clock applet public API."""

from .applet import ClockApplet
from .state import (
    hour_rotation_12h,
    hour_rotation_24h,
    minute_rotation,
)

__all__ = [
    "ClockApplet",
    "hour_rotation_12h",
    "hour_rotation_24h",
    "minute_rotation",
]
