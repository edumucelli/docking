"""Linux CPU, memory, disk and CPU-temperature backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docking.applets.systemmonitor.backend import (
    SystemMonitorBackend,
    TemperatureSource,
)
from docking.applets.systemmonitor.state import (
    CpuSample,
    parse_proc_meminfo,
    parse_proc_stat,
)
from docking.log import get_logger

from .temperature import LinuxTemperatureReader

log = get_logger("systemmonitor.linux")
_PROC_STAT = Path("/proc/stat")
_PROC_MEMINFO = Path("/proc/meminfo")
_PROC_MOUNTS = Path("/proc/mounts")
_SKIP_FS_TYPES = {"squashfs", "tmpfs", "devtmpfs", "overlay", "fuse.snapfuse"}


class LinuxSystemMonitorBackend(SystemMonitorBackend):
    def cpu_sample(self) -> CpuSample | None:
        try:
            return parse_proc_stat(_PROC_STAT.read_text())
        except OSError as exc:
            log.debug("CPU counters unavailable: %s", exc)
            return None

    def memory_fraction(self) -> float | None:
        try:
            return parse_proc_meminfo(_PROC_MEMINFO.read_text())
        except OSError as exc:
            log.debug("Memory counters unavailable: %s", exc)
            return None

    def disk_usage(self) -> list[tuple[str, float]]:
        return disk_usage()

    def temperature_reader(self, **kwargs: Any) -> TemperatureSource:
        return LinuxTemperatureReader(**kwargs)


def disk_usage() -> list[tuple[str, float]]:
    """Return (mountpoint, usage_fraction) for real disk partitions."""
    import os

    result: list[tuple[str, float]] = []
    try:
        with _PROC_MOUNTS.open() as f:
            text = f.read()
    except OSError as exc:
        log.debug("Failed to read %s: %s", _PROC_MOUNTS, exc)
        return result
    seen: set[str] = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        dev, mount, fstype = parts[0], parts[1], parts[2]
        if not dev.startswith("/dev/"):
            continue
        if fstype in _SKIP_FS_TYPES:
            continue
        if dev in seen:
            continue
        seen.add(dev)
        try:
            st = os.statvfs(mount)
        except OSError as exc:
            log.debug("Failed to stat filesystem %s: %s", mount, exc)
            continue
        if st.f_blocks == 0:
            continue
        used = 1.0 - st.f_bavail / st.f_blocks
        result.append((mount, used))
    return result
