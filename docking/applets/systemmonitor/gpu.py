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

"""GPU sampling helpers for the System Monitor applet."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from docking.i18n import _
from docking.log import get_logger

NVIDIA_SMI_BIN = "nvidia-smi"
NVIDIA_SMI_TIMEOUT_S = 0.6
DRM_DIR = Path("/sys/class/drm")
AMD_VENDOR_ID = "0x1002"
AMD_VENDOR_NAME = "Radeon GPU"

log = get_logger("systemmonitor.gpu")


@dataclass(frozen=True, slots=True)
class GpuStats:
    """Minimal GPU state used by the System Monitor tooltip."""

    name: str
    utilization: float | None = None
    memory_used_mib: int | None = None
    memory_total_mib: int | None = None


def parse_nvidia_smi_output(text: str) -> GpuStats | None:
    """Parse ``nvidia-smi`` CSV output."""
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        name, utilization, memory_used, memory_total = parts[:4]
        if not name:
            continue
        return GpuStats(
            name=name,
            utilization=_percent_to_fraction(utilization),
            memory_used_mib=_int_or_none(memory_used),
            memory_total_mib=_int_or_none(memory_total),
        )
    return None


def gpu_summary(stats: GpuStats | None) -> str | None:
    """Build one compact user-facing GPU summary line."""
    if stats is None:
        return None

    parts: list[str] = []
    if stats.utilization is not None:
        parts.append(_("GPU: {pct}%").format(pct=f"{stats.utilization * 100:.0f}"))
    else:
        parts.append(_("GPU: {name}").format(name=stats.name))

    if stats.memory_used_mib is not None and stats.memory_total_mib:
        parts.append(
            _("Mem: {used}/{total} MiB").format(
                used=stats.memory_used_mib,
                total=stats.memory_total_mib,
            )
        )
    elif stats.memory_used_mib is not None:
        parts.append(_("Mem: {used} MiB").format(used=stats.memory_used_mib))

    return " | ".join(parts)


def _percent_to_fraction(value: str) -> float | None:
    try:
        return max(0.0, min(1.0, float(value) / 100.0))
    except ValueError:
        return None


def _int_or_none(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _bytes_to_mib(value: int | None) -> int | None:
    if value is None:
        return None
    return int(value / 1024 / 1024)


class GpuSource(ABC):
    @abstractmethod
    def read(self) -> GpuStats | None: ...
