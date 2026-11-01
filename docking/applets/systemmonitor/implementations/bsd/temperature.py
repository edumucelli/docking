"""BSD temperature source."""

from __future__ import annotations

from docking.applets.thermals.implementations.bsd import backend as thermal_readings

from ...backend import TemperatureSource


class BsdTemperatureReader(TemperatureSource):
    def read(self) -> float | None:
        readings = thermal_readings.temperatures()
        return max(value for _, value in readings) if readings else None
