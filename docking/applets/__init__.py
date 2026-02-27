"""Applet registry -- all available applet types."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docking.applets.base import Applet


@lru_cache(maxsize=1)
def get_registry() -> dict[str, type[Applet]]:
    """Return the applet registry, loading it on first access."""
    from docking.applets.ambient import AmbientApplet
    from docking.applets.applications import ApplicationsApplet
    from docking.applets.battery import BatteryApplet
    from docking.applets.calendar import CalendarApplet
    from docking.applets.clippy import ClippyApplet
    from docking.applets.clock import ClockApplet
    from docking.applets.cpumonitor import CpuMonitorApplet
    from docking.applets.desktop import DesktopApplet
    from docking.applets.hydration import HydrationApplet
    from docking.applets.network import NetworkApplet
    from docking.applets.pomodoro import PomodoroApplet
    from docking.applets.screenshot import ScreenshotApplet
    from docking.applets.separator import SeparatorApplet
    from docking.applets.session import SessionApplet
    from docking.applets.trash import TrashApplet
    from docking.applets.volume import VolumeApplet
    from docking.applets.weather import WeatherApplet
    from docking.applets.workspaces import WorkspacesApplet

    return {
        "ambient": AmbientApplet,
        "applications": ApplicationsApplet,
        "battery": BatteryApplet,
        "calendar": CalendarApplet,
        "clippy": ClippyApplet,
        "clock": ClockApplet,
        "cpumonitor": CpuMonitorApplet,
        "desktop": DesktopApplet,
        "hydration": HydrationApplet,
        "network": NetworkApplet,
        "screenshot": ScreenshotApplet,
        "separator": SeparatorApplet,
        "session": SessionApplet,
        "pomodoro": PomodoroApplet,
        "trash": TrashApplet,
        "volume": VolumeApplet,
        "weather": WeatherApplet,
        "workspaces": WorkspacesApplet,
    }
