"""BSD temperature sensor readings shared by thermal applets."""

from __future__ import annotations

import importlib
import re
import sys
from typing import Any

from docking.applets.thermals.backend import ThermalsBackend
from docking.applets.thermals.state import (
    ThermalReading,
    ThermalSnapshot,
)
from docking.log import get_logger
from docking.platform.commands import read_command as command

log = get_logger("thermals.bsd")


def _psutil() -> Any:
    return importlib.import_module("psutil")


def temperatures() -> list[tuple[str, float]]:
    if sys.platform.startswith("netbsd"):
        from docking.platform.host.implementations.bsd.envsys import read_sensors

        # envsys temperatures are integer microkelvins, even when envstat's
        # human-readable display uses Celsius.
        return [
            (f"{sensor.device}: {sensor.name}", sensor.value / 1_000_000 - 273.15)
            for sensor in read_sensors()
            if sensor.unit == "Temperature" and sensor.value >= 0
        ]
    if sys.platform.startswith("openbsd"):
        output = command(["sysctl", "hw.sensors"]) or ""
        return [
            (name, float(value))
            for name, value in re.findall(
                r"(hw\.sensors\.[^=\s]+)=\s*(-?\d+(?:\.\d+)?) degC", output
            )
        ]
    # psutil added FreeBSD temperature readings after its initial BSD support.
    try:
        reader = getattr(_psutil(), "sensors_temperatures", None)
        if reader is not None:
            return [
                (str(reading.label or chip), float(reading.current))
                for chip, entries in reader().items()
                for reading in entries
            ]
    except Exception as exc:
        log.debug("Native temperature query unavailable: %s", exc)
    value = command(["sysctl", "-n", "dev.cpu.0.temperature"])
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)C", value or "")
    return [("CPU", float(match[1]))] if match else []


class BsdThermalsBackend(ThermalsBackend):
    def get_snapshot(self) -> ThermalSnapshot:
        readings = temperatures()
        hottest = max(readings, key=lambda reading: reading[1]) if readings else None
        return ThermalSnapshot(
            available=True,
            hottest=ThermalReading("", hottest[0], hottest[1]) if hottest else None,
        )
