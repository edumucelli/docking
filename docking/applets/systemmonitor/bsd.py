"""BSD CPU, memory, and disk readings for System Monitor."""

from __future__ import annotations

import importlib
from typing import Any

from docking.log import get_logger

log = get_logger("systemmonitor.bsd")


def _psutil() -> Any:
    return importlib.import_module("psutil")


def cpu_sample() -> tuple[int, int] | None:
    """Return cumulative total/idle milliseconds; never block to sample usage."""
    try:
        times = _psutil().cpu_times()
        return round(sum(float(t) for t in times) * 1000), round(
            float(times.idle) * 1000
        )
    except Exception as exc:
        log.debug("CPU counters unavailable: %s", exc)
        return None


def memory_fraction() -> float | None:
    try:
        return max(0.0, min(1.0, float(_psutil().virtual_memory().percent) / 100))
    except Exception as exc:
        log.debug("Memory counters unavailable: %s", exc)
        return None


_PSEUDO_FS_TYPES = {
    "devfs",
    "fdescfs",
    "procfs",
    "linprocfs",
    "linsysfs",
    "tmpfs",
    "nullfs",
    "mfs",
    "kernfs",
}


def disk_usage() -> list[tuple[str, float]]:
    readings = []
    try:
        util = _psutil()
        partitions = util.disk_partitions(all=True)
    except Exception as exc:
        log.debug("Mount table unavailable: %s", exc)
        return readings
    seen: set[str] = set()
    for partition in partitions:
        path = str(partition.mountpoint)
        if str(partition.fstype) in _PSEUDO_FS_TYPES or path in seen:
            continue
        seen.add(path)
        try:
            readings.append(
                (str(path), float(util.disk_usage(str(path)).percent) / 100)
            )
        except (OSError, ValueError) as exc:
            log.debug("Disk usage unavailable for %s: %s", path, exc)
    return readings
