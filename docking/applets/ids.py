"""Canonical applet identifiers used across registry and UI mappings."""

from __future__ import annotations

from enum import Enum


class AppletId(str, Enum):
    AMBIENT = "ambient"
    APPLICATIONS = "applications"
    BATTERY = "battery"
    CALENDAR = "calendar"
    CLIPPY = "clippy"
    CLOCK = "clock"
    CPUMONITOR = "cpumonitor"
    DESKTOP = "desktop"
    HYDRATION = "hydration"
    NETWORK = "network"
    POMODORO = "pomodoro"
    QUOTE = "quote"
    SCREENSHOT = "screenshot"
    SEPARATOR = "separator"
    SESSION = "session"
    TRASH = "trash"
    VOLUME = "volume"
    WEATHER = "weather"
    WORKSPACES = "workspaces"

    def __str__(self) -> str:
        return self.value
