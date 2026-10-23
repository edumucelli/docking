"""BSD battery readings from ACPI and apm."""

from __future__ import annotations

import importlib
import sys
from typing import Any, NamedTuple

from docking.log import get_logger
from docking.platform.commands import read_command as command

log = get_logger("battery.bsd")


def _psutil() -> Any:
    return importlib.import_module("psutil")


class Battery(NamedTuple):
    capacity: int
    status: str
    seconds_remaining: int | None


def battery() -> Battery | None:
    if sys.platform.startswith("openbsd"):
        percent = command(["apm", "-l"])
        status = command(["apm", "-b"])
        minutes = command(["apm", "-m"])
        ac = command(["apm", "-a"])
        try:
            if status not in {"0", "1", "2", "3"} or percent is None:
                return None
            capacity = int(percent)
            if not 0 <= capacity <= 100:
                return None
            state = "Charging" if status == "3" else "Discharging"
            if ac == "1" and capacity == 100:
                state = "Full"
            remaining = None
            if minutes not in {None, "unknown"}:
                parsed_minutes = int(minutes)
                if parsed_minutes >= 0:
                    remaining = parsed_minutes * 60
            return Battery(capacity, state, remaining)
        except ValueError:
            return None
    try:
        reading = _psutil().sensors_battery()
        if reading is None:
            return None
        capacity = max(0, min(100, round(float(reading.percent))))
        state = "Discharging"
        if reading.power_plugged:
            state = "Full" if capacity == 100 else "Charging"
        seconds = int(reading.secsleft)
        return Battery(capacity, state, seconds if seconds >= 0 else None)
    except Exception as exc:
        log.debug("Battery unavailable: %s", exc)
        return None
