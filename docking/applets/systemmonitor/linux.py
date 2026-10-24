"""Linux CPU, memory, disk and CPU-temperature backend."""

from __future__ import annotations

import shutil
from collections.abc import Callable
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
from docking.applets.systemmonitor.temperature import (
    _HWMON_ROOT,
    _THERMAL_ROOT,
    CommandBackend,
    _run_command,
    discover_available_commands,
    discover_sysfs_temperature_path,
    read_command_temperature,
    read_temperature_file,
)
from docking.log import get_logger

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


class LinuxTemperatureReader(TemperatureSource):
    """Discover and read CPU temperature from sysfs and common CLI tools."""

    def __init__(
        self,
        *,
        thermal_root: Path = _THERMAL_ROOT,
        hwmon_root: Path = _HWMON_ROOT,
        which: Callable[[str], str | None] = shutil.which,
        run_command: Callable[[list[str], float], str | None] | None = None,
    ) -> None:
        self._thermal_root = thermal_root
        self._hwmon_root = hwmon_root
        self._which = which
        self._run_command = run_command or _run_command
        self._sysfs_path: Path | None = None
        self._sysfs_probed = False
        self._command_backends: tuple[CommandBackend, ...] = ()
        self._commands_probed = False

    def read(self) -> float | None:
        """Return current CPU temperature in celsius if one can be found."""
        temperature = self._read_from_sysfs()
        if temperature is not None:
            return temperature
        return self._read_from_commands()

    def _read_from_sysfs(self) -> float | None:
        if self._sysfs_path is None and not self._sysfs_probed:
            self._sysfs_path = discover_sysfs_temperature_path(
                thermal_root=self._thermal_root,
                hwmon_root=self._hwmon_root,
            )
            self._sysfs_probed = True
        if self._sysfs_path is None:
            return None
        temperature = read_temperature_file(self._sysfs_path)
        if temperature is None:
            self._sysfs_path = None
            self._sysfs_probed = False
        return temperature

    def _read_from_commands(self) -> float | None:
        if not self._commands_probed:
            self._command_backends = discover_available_commands(which=self._which)
            self._commands_probed = True
        for backend in self._command_backends:
            temperature = read_command_temperature(
                backend=backend,
                run_command=self._run_command,
            )
            if temperature is not None:
                return temperature
        return None
