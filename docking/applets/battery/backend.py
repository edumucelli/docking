"""Battery reading contract and OS selection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from docking.platform import environment

if TYPE_CHECKING:
    from docking.applets.battery.peripherals import PeripheralBattery
    from docking.applets.battery.state import BatteryState


class BatteryBackend(ABC):
    """Battery readings and actions independent of OS APIs and device paths."""

    @abstractmethod
    def read_battery(self) -> BatteryState | None: ...
    @abstractmethod
    def read_power_watts(self) -> float | None: ...
    @abstractmethod
    def read_peripherals(self) -> list[PeripheralBattery]: ...
    @abstractmethod
    def settings_available(self) -> bool: ...
    @abstractmethod
    def open_settings(self) -> bool: ...


def create_backend() -> BatteryBackend:
    if environment.is_windows():
        from docking.applets.battery.windows import WindowsBatteryBackend

        return WindowsBatteryBackend()
    if environment.is_bsd():
        from docking.applets.battery.bsd import BsdBatteryBackend

        return BsdBatteryBackend()
    from docking.applets.battery.linux import LinuxBatteryBackend

    return LinuxBatteryBackend()
