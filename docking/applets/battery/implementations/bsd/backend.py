"""BSD battery readings from ACPI and apm."""

from __future__ import annotations

from typing import NamedTuple

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
from docking.platform.environment import is_netbsd, is_openbsd


class Battery(NamedTuple):
    capacity: int
    status: str
    seconds_remaining: int | None


def battery() -> Battery | None:
    if is_netbsd():
        from .netbsd import battery as read_battery
    elif is_openbsd():
        from .openbsd import battery as read_battery
    else:
        from .freebsd import battery as read_battery

    return read_battery()


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
