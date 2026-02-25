"""Applet registry -- all available applet types."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docking.applets.base import Applet


@lru_cache(maxsize=1)
def get_registry() -> dict[str, type[Applet]]:
    """Return the applet registry, loading it on first access."""
    from docking.applets.applications import ApplicationsApplet
    from docking.applets.battery import BatteryApplet
    from docking.applets.clippy import ClippyApplet
    from docking.applets.clock import ClockApplet
    from docking.applets.cpumonitor import CpuMonitorApplet
    from docking.applets.desktop import DesktopApplet
    from docking.applets.network import NetworkApplet
    from docking.applets.trash import TrashApplet
    from docking.applets.weather import WeatherApplet

    return {
        "applications": ApplicationsApplet,
        "battery": BatteryApplet,
        "clippy": ClippyApplet,
        "clock": ClockApplet,
        "cpumonitor": CpuMonitorApplet,
        "desktop": DesktopApplet,
        "network": NetworkApplet,
        "trash": TrashApplet,
        "weather": WeatherApplet,
    }
