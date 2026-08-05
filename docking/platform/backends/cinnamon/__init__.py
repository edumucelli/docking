"""Cinnamon Wayland integration."""

from docking.platform.backends.cinnamon.muffin import (
    MuffinDebugClient,
    MuffinWindowService,
)
from docking.platform.backends.cinnamon.session import CinnamonWaylandSessionBackend

__all__ = [
    "CinnamonWaylandSessionBackend",
    "MuffinDebugClient",
    "MuffinWindowService",
]
