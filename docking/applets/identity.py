"""Typed applet identity and categorization helpers."""

from __future__ import annotations

from enum import Enum

APPLET_PREFIX = "applet://"


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


class AppletCategory(str, Enum):
    LAUNCHER = "Launcher & Navigation"
    PRODUCTIVITY = "Time & Productivity"
    SYSTEM = "System & Power"
    WELLNESS = "Wellness & Ambient"
    INFORMATION = "Information & Monitoring"
    OTHER = "Other"


APPLET_CATEGORY_ORDER: tuple[AppletCategory, ...] = (
    AppletCategory.LAUNCHER,
    AppletCategory.PRODUCTIVITY,
    AppletCategory.SYSTEM,
    AppletCategory.WELLNESS,
    AppletCategory.INFORMATION,
    AppletCategory.OTHER,
)

APPLET_CATEGORY_BY_ID: dict[AppletId, AppletCategory] = {
    AppletId.APPLICATIONS: AppletCategory.LAUNCHER,
    AppletId.DESKTOP: AppletCategory.LAUNCHER,
    AppletId.WORKSPACES: AppletCategory.LAUNCHER,
    AppletId.CALENDAR: AppletCategory.PRODUCTIVITY,
    AppletId.CLOCK: AppletCategory.PRODUCTIVITY,
    AppletId.CLIPPY: AppletCategory.PRODUCTIVITY,
    AppletId.POMODORO: AppletCategory.PRODUCTIVITY,
    AppletId.BATTERY: AppletCategory.SYSTEM,
    AppletId.NETWORK: AppletCategory.SYSTEM,
    AppletId.SCREENSHOT: AppletCategory.SYSTEM,
    AppletId.SESSION: AppletCategory.SYSTEM,
    AppletId.TRASH: AppletCategory.SYSTEM,
    AppletId.VOLUME: AppletCategory.SYSTEM,
    AppletId.AMBIENT: AppletCategory.WELLNESS,
    AppletId.HYDRATION: AppletCategory.WELLNESS,
    AppletId.CPUMONITOR: AppletCategory.INFORMATION,
    AppletId.QUOTE: AppletCategory.INFORMATION,
    AppletId.WEATHER: AppletCategory.INFORMATION,
}


def parse_applet_id(desktop_id: str) -> AppletId | None:
    """Parse `applet://...` desktop ids into AppletId.

    Supports instance suffixes like `applet://separator#2`.
    Returns None for non-applet IDs or unknown applet IDs.
    """
    if not desktop_id.startswith(APPLET_PREFIX):
        return None
    raw = desktop_id[len(APPLET_PREFIX) :]
    raw_id = raw.split("#", 1)[0]
    try:
        return AppletId(raw_id)
    except ValueError:
        return None


def applet_id_from(desktop_id: str) -> AppletId:
    """Extract AppletId from desktop id.

    Raises ValueError for non-applet ids or unknown applet ids.
    """
    parsed = parse_applet_id(desktop_id=desktop_id)
    if parsed is None:
        raise ValueError(f"Invalid applet desktop id: {desktop_id}")
    return parsed


def is_applet_desktop_id(desktop_id: str) -> bool:
    """True if desktop_id has applet prefix."""
    return desktop_id.startswith(APPLET_PREFIX)


def applet_desktop_id(applet_id: AppletId, instance: int | None = None) -> str:
    """Build a canonical applet desktop id, optionally with instance suffix."""
    if instance is None:
        return f"{APPLET_PREFIX}{applet_id}"
    return f"{APPLET_PREFIX}{applet_id}#{instance}"


def category_for(applet_id: AppletId) -> AppletCategory:
    """Resolve applet category for menu grouping."""
    return APPLET_CATEGORY_BY_ID.get(applet_id, AppletCategory.OTHER)
