"""FreeBSD battery readings from psutil's native ACPI support."""

from __future__ import annotations

import importlib
from typing import Any

from docking.log import get_logger

from .backend import Battery

log = get_logger("battery.bsd")


def _psutil() -> Any:
    return importlib.import_module("psutil")


def battery() -> Battery | None:
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
