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

"""Pure state helpers for Clock applet."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any

from docking.i18n import _

# Default preferences
DEFAULT_PREFS: dict[str, Any] = {
    "show_digital": False,
    "show_military": False,
    "show_date": False,
    "show_seconds": False,
    "alarm_target": None,
}


def minute_rotation(minute: int) -> float:
    """Rotation angle (radians) for the minute hand."""
    return math.pi * (minute / 30.0 + 1.0)


def seconds_rotation(second: int) -> float:
    """Rotation angle (radians) for the seconds hand."""
    return math.pi * (second / 30.0 + 1.0)


def hour_rotation_12h(hour: int, minute: int) -> float:
    """Rotation angle (radians) for the hour hand in 12-hour mode."""
    return math.pi * (hour % 12 / 6.0 + minute / 360.0 + 1.0)


def hour_rotation_24h(hour: int, minute: int) -> float:
    """Rotation angle (radians) for the hour hand in 24-hour mode."""
    return math.pi * (hour % 24 / 12.0 + minute / 720.0 + 1.0)


def normalize_alarm_target(value: object, *, now_ts: float) -> int | None:
    """Return a future one-shot alarm target, or ``None`` when unusable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        target = value
    elif isinstance(value, float):
        target = int(value)
    else:
        return None
    return target if target > int(now_ts) else None


def compute_alarm_target(*, now_ts: float, hour: int, minute: int) -> int:
    """Return the next timestamp for the requested wall-clock time."""
    now_local = time.localtime(now_ts)
    target = time.mktime(
        (
            now_local.tm_year,
            now_local.tm_mon,
            now_local.tm_mday,
            hour,
            minute,
            0,
            now_local.tm_wday,
            now_local.tm_yday,
            now_local.tm_isdst,
        )
    )
    if target <= now_ts:
        target += 24 * 60 * 60
    return int(target)


def load_prefs(
    prefs: Mapping[str, Any] | None,
    *,
    now_ts: float | None = None,
) -> tuple[bool, bool, bool, bool, int | None]:
    """Load display prefs from persisted applet prefs mapping."""
    current_ts = time.time() if now_ts is None else now_ts
    if not prefs:
        return (
            bool(DEFAULT_PREFS["show_digital"]),
            bool(DEFAULT_PREFS["show_military"]),
            bool(DEFAULT_PREFS["show_date"]),
            bool(DEFAULT_PREFS["show_seconds"]),
            normalize_alarm_target(DEFAULT_PREFS["alarm_target"], now_ts=current_ts),
        )
    return (
        bool(prefs.get("show_digital", DEFAULT_PREFS["show_digital"])),
        bool(prefs.get("show_military", DEFAULT_PREFS["show_military"])),
        bool(prefs.get("show_date", DEFAULT_PREFS["show_date"])),
        bool(prefs.get("show_seconds", DEFAULT_PREFS["show_seconds"])),
        normalize_alarm_target(
            prefs.get("alarm_target", DEFAULT_PREFS["alarm_target"]),
            now_ts=current_ts,
        ),
    )


def check_alarm(*, now_ts: float, alarm_target: int | None) -> bool:
    """Return whether a one-shot alarm should fire at ``now_ts``."""
    return alarm_target is not None and now_ts >= alarm_target


def build_tooltip(
    now: time.struct_time,
    is_24h: bool,
    *,
    alarm_target: int | None = None,
) -> str:
    """Build tooltip date/time string for current mode."""
    if is_24h:
        current = time.strftime("%a, %b %-d %H:%M", now)
    else:
        current = time.strftime("%a, %b %-d %-I:%M %p", now)
    if alarm_target is None:
        return current

    alarm_now = time.localtime(alarm_target)
    if is_24h:
        alarm_text = time.strftime("%H:%M", alarm_now)
    else:
        alarm_text = time.strftime("%-I:%M %p", alarm_now)
    return f"{current}\n{_('Alarm: {time}').format(time=alarm_text)}"


def save_payload(
    show_digital: bool,
    show_military: bool,
    show_date: bool,
    show_seconds: bool,
    alarm_target: int | None,
) -> dict[str, bool | int | None]:
    """Return preferences payload to persist."""
    return {
        "show_digital": show_digital,
        "show_military": show_military,
        "show_date": show_date,
        "show_seconds": show_seconds,
        "alarm_target": alarm_target,
    }
