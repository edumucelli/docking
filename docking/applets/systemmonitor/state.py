"""State and parsing helpers for System Monitor applet."""

from __future__ import annotations

import colorsys
from pathlib import Path
from typing import NamedTuple

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
) -> str:
    """Build tooltip text for current cpu/memory values."""
    text = _("CPU: {cpu}% | Mem: {mem}%").format(
        cpu=f"{cpu * 100:.1f}", mem=f"{mem * 100:.1f}"
    )
    if temperature_c is not None:
        text = _("{text} | Temp: {temp}°C").format(
            text=text,
            temp=f"{temperature_c:.1f}",
        )
    if disks:
        parts = [f"{mount}: {pct * 100:.0f}%" for mount, pct in disks]
        text += "\n" + _("Disk: {usage}").format(usage="  ".join(parts))
    return text
