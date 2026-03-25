"""Stable applet identifiers and menu-grouping helpers.

Why applet identity needs its own module

Applets appear in several places at once:

- persisted dock configuration,
- runtime item models,
- settings menus,
- catalog lookups,
- user-facing grouping in the applet picker.

Those layers need one shared notion of "which applet is this?". If each layer
reconstructed ids from strings ad hoc, subtle mismatches would accumulate and
saved configurations would become brittle.

The ``applet://`` desktop-id scheme

Docking models applets alongside launchers and files, so applets need a desktop
id that can live in the same pinned-item list. The custom ``applet://`` prefix
creates that shared namespace while still making applets easy to recognize.
Optional ``#<instance>`` suffixes let the same applet type appear multiple
 times when the UX allows it, such as separators.

What this module owns

- the canonical ``AppletId`` enum,
- category grouping used by the settings UI and documentation,
- parser/builders for ``applet://...`` desktop ids.

That makes this file the contract boundary between persistent config strings and
higher-level applet objects.
"""

from __future__ import annotations

from enum import Enum

APPLET_PREFIX = "applet://"


class AppletId(str, Enum):
    AMBIENT = "ambient"
    APPLICATIONS = "applications"
    BATTERY = "battery"
    BOOKMARKS = "bookmarks"
    BLUETOOTH = "bluetooth"
    BRIGHTNESS = "brightness"
    CALCULATOR = "calculator"
    CALENDAR = "calendar"
    CLIPPY = "clippy"
    CLOCK = "clock"
    COLORPICKER = "colorpicker"
    SYSTEMMONITOR = "systemmonitor"
    DESKTOP = "desktop"
    HYDRATION = "hydration"
    KEYBOARDLAYOUT = "keyboardlayout"
    NETWORK = "network"
    MOON = "moon"
    QUICKNOTE = "quicknote"
    MUSIC = "music"
    NOTIFICATIONS = "notifications"
    PET = "pet"
    POWERPROFILES = "powerprofiles"
    POMODORO = "pomodoro"
    QUOTE = "quote"
    RECENTFILES = "recentfiles"
    SCREENSHOT = "screenshot"
    SEPARATOR = "separator"
    SESSION = "session"
    STRETCHCOACH = "stretchcoach"
    TODAYINHISTORY = "todayinhistory"
    TRIVIA = "trivia"
    TRASH = "trash"
    UNITCONVERTER = "unitconverter"
    URLSHORTENER = "urlshortener"
    VOLUME = "volume"
    WEATHER = "weather"
    WINDOWKILLER = "windowkiller"
    WORKSPACES = "workspaces"

    def __str__(self) -> str:
        return self.value


class AppletCategory(str, Enum):
    LAUNCHER = "Launcher & Navigation"
    PRODUCTIVITY = "Time & Productivity"
    SYSTEM = "System & Power"
    WELLNESS = "Wellness & Ambient"
    INFORMATION = "Information and Environment"
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
    AppletId.CALCULATOR: AppletCategory.PRODUCTIVITY,
    AppletId.CALENDAR: AppletCategory.PRODUCTIVITY,
    AppletId.CLOCK: AppletCategory.PRODUCTIVITY,
    AppletId.CLIPPY: AppletCategory.PRODUCTIVITY,
    AppletId.BOOKMARKS: AppletCategory.PRODUCTIVITY,
    AppletId.COLORPICKER: AppletCategory.PRODUCTIVITY,
    AppletId.POMODORO: AppletCategory.PRODUCTIVITY,
    AppletId.QUICKNOTE: AppletCategory.PRODUCTIVITY,
    AppletId.RECENTFILES: AppletCategory.PRODUCTIVITY,
    AppletId.BATTERY: AppletCategory.SYSTEM,
    AppletId.BLUETOOTH: AppletCategory.SYSTEM,
    AppletId.BRIGHTNESS: AppletCategory.SYSTEM,
    AppletId.KEYBOARDLAYOUT: AppletCategory.SYSTEM,
    AppletId.NETWORK: AppletCategory.SYSTEM,
    AppletId.NOTIFICATIONS: AppletCategory.SYSTEM,
    AppletId.POWERPROFILES: AppletCategory.SYSTEM,
    AppletId.MOON: AppletCategory.INFORMATION,
    AppletId.MUSIC: AppletCategory.SYSTEM,
    AppletId.SCREENSHOT: AppletCategory.SYSTEM,
    AppletId.SESSION: AppletCategory.SYSTEM,
    AppletId.TRASH: AppletCategory.SYSTEM,
    AppletId.VOLUME: AppletCategory.SYSTEM,
    AppletId.AMBIENT: AppletCategory.WELLNESS,
    AppletId.HYDRATION: AppletCategory.WELLNESS,
    AppletId.PET: AppletCategory.WELLNESS,
    AppletId.STRETCHCOACH: AppletCategory.WELLNESS,
    AppletId.SYSTEMMONITOR: AppletCategory.SYSTEM,
    AppletId.UNITCONVERTER: AppletCategory.PRODUCTIVITY,
    AppletId.URLSHORTENER: AppletCategory.PRODUCTIVITY,
    AppletId.WINDOWKILLER: AppletCategory.SYSTEM,
    AppletId.QUOTE: AppletCategory.INFORMATION,
    AppletId.TODAYINHISTORY: AppletCategory.INFORMATION,
    AppletId.TRIVIA: AppletCategory.INFORMATION,
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
