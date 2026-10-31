"""Battery reading contract and OS selection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docking.applets.battery.peripherals import PeripheralBattery
    from docking.applets.battery.state import BatteryState


class BatteryBackend(ABC):
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
