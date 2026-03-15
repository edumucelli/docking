"""Applet catalog and lazy applet class loading."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache, lru_cache
from importlib import import_module
from typing import TYPE_CHECKING

from docking.applets.identity import AppletId

if TYPE_CHECKING:
    from docking.applets.base import Applet


@dataclass(frozen=True, slots=True)
class AppletCatalogEntry:
    """Static metadata for one applet type."""

    applet_id: AppletId
    name: str
    module_path: str
    class_name: str


@lru_cache(maxsize=1)
def get_applet_catalog() -> dict[AppletId, AppletCatalogEntry]:
    """Return applet metadata without importing any applet modules."""
    return {
        AppletId.AMBIENT: AppletCatalogEntry(
            applet_id=AppletId.AMBIENT,
            name="Ambient",
            module_path="docking.applets.ambient.applet",
            class_name="AmbientApplet",
        ),
        AppletId.APPLICATIONS: AppletCatalogEntry(
            applet_id=AppletId.APPLICATIONS,
            name="Applications",
            module_path="docking.applets.applications.applet",
            class_name="ApplicationsApplet",
        ),
        AppletId.BATTERY: AppletCatalogEntry(
            applet_id=AppletId.BATTERY,
            name="Battery",
            module_path="docking.applets.battery.applet",
            class_name="BatteryApplet",
        ),
        AppletId.BOOKMARKS: AppletCatalogEntry(
            applet_id=AppletId.BOOKMARKS,
            name="Bookmarks",
            module_path="docking.applets.bookmarks.applet",
            class_name="BookmarksApplet",
        ),
        AppletId.BLUETOOTH: AppletCatalogEntry(
            applet_id=AppletId.BLUETOOTH,
            name="Bluetooth",
            module_path="docking.applets.bluetooth.applet",
            class_name="BluetoothApplet",
        ),
        AppletId.BRIGHTNESS: AppletCatalogEntry(
            applet_id=AppletId.BRIGHTNESS,
            name="Brightness",
            module_path="docking.applets.brightness.applet",
            class_name="BrightnessApplet",
        ),
        AppletId.CALENDAR: AppletCatalogEntry(
            applet_id=AppletId.CALENDAR,
            name="Calendar",
            module_path="docking.applets.calendar.applet",
            class_name="CalendarApplet",
        ),
        AppletId.CLIPPY: AppletCatalogEntry(
            applet_id=AppletId.CLIPPY,
            name="Clippy",
            module_path="docking.applets.clippy.applet",
            class_name="ClippyApplet",
        ),
        AppletId.CLOCK: AppletCatalogEntry(
            applet_id=AppletId.CLOCK,
            name="Clock",
            module_path="docking.applets.clock.applet",
            class_name="ClockApplet",
        ),
        AppletId.COLORPICKER: AppletCatalogEntry(
            applet_id=AppletId.COLORPICKER,
            name="Color Picker",
            module_path="docking.applets.colorpicker.applet",
            class_name="ColorPickerApplet",
        ),
        AppletId.CPUMONITOR: AppletCatalogEntry(
            applet_id=AppletId.CPUMONITOR,
            name="CPU Monitor",
            module_path="docking.applets.cpumonitor.applet",
            class_name="CpuMonitorApplet",
        ),
        AppletId.DESKTOP: AppletCatalogEntry(
            applet_id=AppletId.DESKTOP,
            name="Desktop",
            module_path="docking.applets.desktop.applet",
            class_name="DesktopApplet",
        ),
        AppletId.HYDRATION: AppletCatalogEntry(
            applet_id=AppletId.HYDRATION,
            name="Hydration",
            module_path="docking.applets.hydration.applet",
            class_name="HydrationApplet",
        ),
        AppletId.KEYBOARDLAYOUT: AppletCatalogEntry(
            applet_id=AppletId.KEYBOARDLAYOUT,
            name="Keyboard Layout",
            module_path="docking.applets.keyboardlayout.applet",
            class_name="KeyboardLayoutApplet",
        ),
        AppletId.MOON: AppletCatalogEntry(
            applet_id=AppletId.MOON,
            name="Moon",
            module_path="docking.applets.moon.applet",
            class_name="MoonApplet",
        ),
        AppletId.MUSIC: AppletCatalogEntry(
            applet_id=AppletId.MUSIC,
            name="Music",
            module_path="docking.applets.music.applet",
            class_name="MusicApplet",
        ),
        AppletId.NETWORK: AppletCatalogEntry(
            applet_id=AppletId.NETWORK,
            name="Network",
            module_path="docking.applets.network.applet",
            class_name="NetworkApplet",
        ),
        AppletId.NOTIFICATIONS: AppletCatalogEntry(
            applet_id=AppletId.NOTIFICATIONS,
            name="Notifications",
            module_path="docking.applets.notifications.applet",
            class_name="NotificationsApplet",
        ),
        AppletId.PET: AppletCatalogEntry(
            applet_id=AppletId.PET,
            name="Pet",
            module_path="docking.applets.pet.applet",
            class_name="PetApplet",
        ),
        AppletId.POMODORO: AppletCatalogEntry(
            applet_id=AppletId.POMODORO,
            name="Pomodoro",
            module_path="docking.applets.pomodoro.applet",
            class_name="PomodoroApplet",
        ),
        AppletId.POWERPROFILES: AppletCatalogEntry(
            applet_id=AppletId.POWERPROFILES,
            name="Power Profiles",
            module_path="docking.applets.powerprofiles.applet",
            class_name="PowerProfilesApplet",
        ),
        AppletId.QUICKNOTE: AppletCatalogEntry(
            applet_id=AppletId.QUICKNOTE,
            name="Quick Note",
            module_path="docking.applets.quicknote.applet",
            class_name="QuickNoteApplet",
        ),
        AppletId.QUOTE: AppletCatalogEntry(
            applet_id=AppletId.QUOTE,
            name="Quote",
            module_path="docking.applets.quote.applet",
            class_name="QuoteApplet",
        ),
        AppletId.RECENTFILES: AppletCatalogEntry(
            applet_id=AppletId.RECENTFILES,
            name="Recent Files",
            module_path="docking.applets.recentfiles.applet",
            class_name="RecentFilesApplet",
        ),
        AppletId.SCREENSHOT: AppletCatalogEntry(
            applet_id=AppletId.SCREENSHOT,
            name="Screenshot",
            module_path="docking.applets.screenshot.applet",
            class_name="ScreenshotApplet",
        ),
        AppletId.SEPARATOR: AppletCatalogEntry(
            applet_id=AppletId.SEPARATOR,
            name="Separator",
            module_path="docking.applets.separator.applet",
            class_name="SeparatorApplet",
        ),
        AppletId.SESSION: AppletCatalogEntry(
            applet_id=AppletId.SESSION,
            name="Session",
            module_path="docking.applets.session.applet",
            class_name="SessionApplet",
        ),
        AppletId.STRETCHCOACH: AppletCatalogEntry(
            applet_id=AppletId.STRETCHCOACH,
            name="Stretch Coach",
            module_path="docking.applets.stretchcoach.applet",
            class_name="StretchCoachApplet",
        ),
        AppletId.TODAYINHISTORY: AppletCatalogEntry(
            applet_id=AppletId.TODAYINHISTORY,
            name="Today in History",
            module_path="docking.applets.todayinhistory.applet",
            class_name="TodayInHistoryApplet",
        ),
        AppletId.TRASH: AppletCatalogEntry(
            applet_id=AppletId.TRASH,
            name="Trash",
            module_path="docking.applets.trash.applet",
            class_name="TrashApplet",
        ),
        AppletId.TRIVIA: AppletCatalogEntry(
            applet_id=AppletId.TRIVIA,
            name="Random Trivia",
            module_path="docking.applets.trivia.applet",
            class_name="TriviaApplet",
        ),
        AppletId.VOLUME: AppletCatalogEntry(
            applet_id=AppletId.VOLUME,
            name="Volume",
            module_path="docking.applets.volume.applet",
            class_name="VolumeApplet",
        ),
        AppletId.WEATHER: AppletCatalogEntry(
            applet_id=AppletId.WEATHER,
            name="Weather",
            module_path="docking.applets.weather.applet",
            class_name="WeatherApplet",
        ),
        AppletId.WORKSPACES: AppletCatalogEntry(
            applet_id=AppletId.WORKSPACES,
            name="Workspaces",
            module_path="docking.applets.workspaces.applet",
            class_name="WorkspacesApplet",
        ),
    }


@cache
def load_applet_class(applet_id: AppletId) -> type[Applet] | None:
    """Import and return a specific applet class on demand."""
    entry = get_applet_catalog().get(applet_id)
    if entry is None:
        return None
    module = import_module(entry.module_path)
    return getattr(module, entry.class_name)
