"""Unavailable telemetry in the current Windows port; never probe procfs/sysfs."""

from docking.applets.systemmonitor.backend import (
    SystemMonitorBackend,
    TemperatureSource,
)
from docking.applets.systemmonitor.gpu import GpuSource, GpuStats
from docking.applets.systemmonitor.state import CpuSample


class UnavailableTemperatureSource(TemperatureSource):
    def read(self) -> float | None:
        return None


class UnavailableGpuSource(GpuSource):
    def read(self) -> GpuStats | None:
        return None


class WindowsSystemMonitorBackend(SystemMonitorBackend):
    def cpu_sample(self) -> CpuSample | None:
        return None

    def memory_fraction(self) -> float | None:
        return None

    def disk_usage(self) -> list[tuple[str, float]]:
        return []

    def temperature_reader(self, **kwargs: object) -> TemperatureSource:
        return UnavailableTemperatureSource()
