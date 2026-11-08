"""FreeBSD CPU temperatures from psutil or native sysctl."""

from __future__ import annotations

import importlib
import re
from typing import Any

from docking.log import get_logger
from docking.platform.commands import read_command as command

log = get_logger("thermals.bsd")


def _psutil() -> Any:
    return importlib.import_module("psutil")


def temperatures() -> list[tuple[str, float]]:
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
