"""Battery reading contract and OS selection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from docking.platform import environment

if TYPE_CHECKING:
    from docking.applets.battery.state import BatteryState


class BatteryBackend(ABC):
    @abstractmethod
    def read_battery(self, bat_name: str, base: Path) -> BatteryState | None: ...
    @abstractmethod
    def read_power_watts(self, bat_name: str, base: Path) -> float | None: ...


def create_backend(*, base: Path = Path("/sys/class/power_supply")) -> BatteryBackend:
    if environment.is_windows() and base == Path("/sys/class/power_supply"):
        from docking.applets.battery.windows import WindowsBatteryBackend

        return WindowsBatteryBackend()
    if environment.is_bsd() and base == Path("/sys/class/power_supply"):
        from docking.applets.battery.bsd import BsdBatteryBackend

        return BsdBatteryBackend()
    from docking.applets.battery.linux import LinuxBatteryBackend

    return LinuxBatteryBackend()
