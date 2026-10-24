"""System Monitor reading contract and OS selection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from docking.platform import environment

if TYPE_CHECKING:
    from docking.applets.systemmonitor.state import CpuSample


class TemperatureSource(ABC):
    @abstractmethod
    def read(self) -> float | None: ...


class SystemMonitorBackend(ABC):
    @abstractmethod
    def cpu_sample(self) -> CpuSample | None: ...
    @abstractmethod
    def memory_fraction(self) -> float | None: ...
    @abstractmethod
    def disk_usage(self) -> list[tuple[str, float]]: ...
    @abstractmethod
    def temperature_reader(self, **kwargs: Any) -> TemperatureSource: ...


def create_backend() -> SystemMonitorBackend:
    if environment.is_bsd():
        from docking.applets.systemmonitor.bsd import BsdSystemMonitorBackend

        return BsdSystemMonitorBackend()
    from docking.applets.systemmonitor.linux import LinuxSystemMonitorBackend

    return LinuxSystemMonitorBackend()
