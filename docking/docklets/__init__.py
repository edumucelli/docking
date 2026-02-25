"""Docklet registry -- all available docklet types."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docking.docklets.base import Docklet


@lru_cache(maxsize=1)
def get_registry() -> dict[str, type[Docklet]]:
    """Return the docklet registry, loading it on first access."""
    from docking.docklets.applications import ApplicationsDocklet
    from docking.docklets.battery import BatteryDocklet
    from docking.docklets.clippy import ClippyDocklet
    from docking.docklets.clock import ClockDocklet
    from docking.docklets.cpumonitor import CpuMonitorDocklet
    from docking.docklets.desktop import DesktopDocklet
    from docking.docklets.trash import TrashDocklet
    from docking.docklets.weather import WeatherDocklet

    return {
        "applications": ApplicationsDocklet,
        "battery": BatteryDocklet,
        "clippy": ClippyDocklet,
        "clock": ClockDocklet,
        "cpumonitor": CpuMonitorDocklet,
        "desktop": DesktopDocklet,
        "trash": TrashDocklet,
        "weather": WeatherDocklet,
    }
