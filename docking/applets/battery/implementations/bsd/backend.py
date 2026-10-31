"""BSD battery readings from ACPI and apm."""

from __future__ import annotations

import importlib
import sys
from typing import Any, NamedTuple

from docking.applets.battery.backend import BatteryBackend
from docking.applets.battery.implementations.unix.desktop_settings import (
    open_power_settings,
    power_settings_command,
)
from docking.applets.battery.implementations.unix.upower import (
    read_peripheral_batteries,
)
from docking.applets.battery.peripherals import PeripheralBattery
from docking.applets.battery.state import BatteryState, resolve_battery_icon
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
            if status == "3":
                state = "Charging"
            elif ac == "1":
                state = "Full"
            else:
                state = "Discharging"
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


class BsdBatteryBackend(BatteryBackend):
    def read_battery(self) -> BatteryState | None:
        reading = battery()
        if reading is None:
            return None
        capacity, status, remaining = reading
        level = (
            "full"
            if capacity >= 95
            else "high"
            if capacity >= 60
            else "normal"
            if capacity >= 30
            else "low"
            if capacity >= 10
            else "critical"
        )
        return BatteryState(
            resolve_battery_icon(level, status), capacity, status, remaining
        )

    def read_power_watts(self) -> float | None:
        return None

    def read_peripherals(self) -> list[PeripheralBattery]:
        return read_peripheral_batteries()

    def settings_available(self) -> bool:
        return power_settings_command() is not None

    def open_settings(self) -> bool:
        return open_power_settings()
