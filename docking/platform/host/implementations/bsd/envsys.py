"""Read NetBSD's typed envsys property list without parsing display columns."""

from __future__ import annotations

import plistlib
from dataclasses import dataclass
from xml.parsers.expat import ExpatError

from docking.log import get_logger
from docking.platform.commands import read_command

log = get_logger("envsys")


@dataclass(frozen=True)
class Sensor:
    device: str
    name: str
    unit: str
    value: int
    maximum: int | None


def parse_sensors(output: str) -> tuple[Sensor, ...]:
    """Keep valid sensor values, including warning and critical readings."""
    try:
        devices = plistlib.loads(output.encode())
    except (ValueError, TypeError, OverflowError, ExpatError) as exc:
        log.debug("Invalid envsys property list: %s", exc)
        return ()
    if not isinstance(devices, dict):
        return ()
    sensors = []
    for device, entries in devices.items():
        if not isinstance(device, str) or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            state = entry.get("state")
            if not isinstance(state, str) or state not in {
                "valid",
                "critical",
                "critical-under",
                "critical-over",
                "warning-under",
                "warning-over",
            }:
                continue
            name, unit = entry.get("description"), entry.get("type")
            value, maximum = entry.get("cur-value"), entry.get("max-value")
            if (
                not isinstance(name, str)
                or not isinstance(unit, str)
                or type(value) is not int
            ):
                continue
            sensors.append(
                Sensor(
                    device, name, unit, value, maximum if type(maximum) is int else None
                )
            )
    return tuple(sensors)


def read_sensors() -> tuple[Sensor, ...]:
    output = read_command(["envstat", "-x"])
    return parse_sensors(output) if output else ()
