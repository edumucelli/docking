# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""State and parsing helpers for System Monitor applet."""

from __future__ import annotations

import colorsys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from docking.applets.systemmonitor.gpu import GpuStats, gpu_summary
from docking.applets.temperature import (
    TemperatureUnit,
    format_temperature,
    normalize_temperature_unit,
)
from docking.applets.tooltip import structured_tooltip
from docking.i18n import _
from docking.log import get_logger

# Redraw thresholds (avoid excessive redraws)
CPU_THRESHOLD = 0.03
MEM_THRESHOLD = 0.01
log = get_logger("systemmonitor.state")


class CpuSample(NamedTuple):
    """Raw CPU jiffies from /proc/stat."""

    total: int
    idle: int


@dataclass(frozen=True, slots=True)
class SystemMonitorPrefs:
    """Persisted System Monitor applet preferences."""

    show_disk: bool = True
    temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS


def prefs_from_mapping(prefs: Mapping[str, Any] | None) -> SystemMonitorPrefs:
    """Build preferences from persisted values."""
    if not prefs:
        return SystemMonitorPrefs()
    return SystemMonitorPrefs(
        show_disk=bool(prefs.get("show_disk", True)),
        temperature_unit=normalize_temperature_unit(prefs.get("temperature_unit")),
    )


def prefs_payload(
    *,
    show_disk: bool,
    temperature_unit: TemperatureUnit,
) -> dict[str, object]:
    """Build payload used by save_prefs()."""
    return {
        "show_disk": show_disk,
        "temperature_unit": normalize_temperature_unit(temperature_unit).value,
    }


def parse_proc_stat(text: str) -> CpuSample:
    """Parse first line of /proc/stat into total and idle jiffies."""
    # cpu  user nice system idle iowait irq softirq [steal guest guest_nice]
    parts = text.split("\n")[0].split()
    values = [int(v) for v in parts[1:8]]
    user, nice, system, idle, iowait, irq, softirq = values
    total = user + nice + system + idle + iowait + irq + softirq
    idle_total = idle + iowait
    return CpuSample(total, idle_total)


def cpu_percent(prev: CpuSample, curr: CpuSample) -> float:
    """Compute CPU usage fraction (0.0-1.0) from two samples."""
    total_diff = curr.total - prev.total
    if total_diff == 0:
        return 0.0
    idle_diff = curr.idle - prev.idle
    return 1.0 - idle_diff / total_diff


def parse_proc_meminfo(text: str) -> float:
    """Parse /proc/meminfo, return memory usage fraction (0.0-1.0)."""
    mem_total = 0
    mem_available = 0
    for line in text.split("\n"):
        if line.startswith("MemTotal:"):
            mem_total = int(line.split()[1])
        elif line.startswith("MemAvailable:"):
            mem_available = int(line.split()[1])
    if mem_total == 0:
        return 0.0
    return 1.0 - mem_available / mem_total


def cpu_hue_rgb(cpu: float) -> tuple[float, float, float]:
    """Map CPU usage to color: green (0%) -> red (100%)."""
    hue = (1.0 - cpu) * 120.0 / 360.0
    return colorsys.hsv_to_rgb(hue, 1.0, 1.0)


_SKIP_FS_TYPES = {"squashfs", "tmpfs", "devtmpfs", "overlay", "fuse.snapfuse"}
_PROC_MOUNTS = Path("/proc/mounts")


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


def tooltip_text(
    cpu: float,
    mem: float,
    temperature_c: float | None = None,
    disks: list[tuple[str, float]] | None = None,
    gpu: GpuStats | None = None,
    temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
) -> str:
    """Build tooltip text for current cpu/memory values."""
    primary = _("CPU: {cpu}% | Mem: {mem}%").format(
        cpu=f"{cpu * 100:.1f}", mem=f"{mem * 100:.1f}"
    )
    if temperature_c is not None:
        primary = _("{text} | Temp: {temp}").format(
            text=primary,
            temp=format_temperature(
                temperature_c,
                temperature_unit=temperature_unit,
                precision=1,
            ),
        )
    details = []
    gpu_line = gpu_summary(gpu)
    if gpu_line:
        details.append(gpu_line)
    if disks:
        parts = [f"{mount}: {pct * 100:.0f}%" for mount, pct in disks]
        details.append(_("Disk: {usage}").format(usage="  ".join(parts)))
    return structured_tooltip(
        title=_("System Monitor"),
        primary=primary,
        details=details,
    )
