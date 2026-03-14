"""State and grouping logic for Applications applet."""

from __future__ import annotations

from collections import defaultdict

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio

# FreeDesktop main categories -> display label
CATEGORY_LABELS: dict[str, str] = {
    "AudioVideo": "Multimedia",
    "Audio": "Multimedia",
    "Video": "Multimedia",
    "Development": "Development",
    "Education": "Education",
    "Game": "Games",
    "Graphics": "Graphics",
    "Network": "Internet",
    "Office": "Office",
    "Science": "Science",
    "Settings": "Settings",
    "System": "System",
    "Utility": "Accessories",
}

# Category -> icon name for submenu
CATEGORY_ICONS: dict[str, str] = {
    "Multimedia": "applications-multimedia",
    "Development": "applications-development",
    "Education": "applications-science",
    "Games": "applications-games",
    "Graphics": "applications-graphics",
    "Internet": "applications-internet",
    "Office": "applications-office",
    "Science": "applications-science",
    "Settings": "preferences-system",
    "System": "applications-system",
    "Accessories": "applications-utilities",
}


def _map_category(categories: str) -> str:
    """Map FreeDesktop category string to display category."""
    display_cat = "Other"
    for raw_cat in categories.split(";"):
        if raw_cat in CATEGORY_LABELS:
            display_cat = CATEGORY_LABELS[raw_cat]
            break
    return display_cat


def _build_app_categories() -> dict[str, list[Gio.DesktopAppInfo]]:
    """Group installed apps by FreeDesktop category.

    Returns {display_category: [app_info, ...]} sorted by app name.
    Apps that don't match any known category go into "Other".
    Hidden and no-display apps are excluded.
    """
    categories: dict[str, list[Gio.DesktopAppInfo]] = defaultdict(list)

    for app_info in Gio.AppInfo.get_all():
        if not isinstance(app_info, Gio.DesktopAppInfo):
            continue
        if app_info.get_is_hidden() or app_info.get_nodisplay():
            continue

        cats = app_info.get_categories() or ""
        categories[_map_category(categories=cats)].append(app_info)

    # Sort apps within each category by display name
    for apps in categories.values():
        apps.sort(key=lambda a: (a.get_display_name() or "").lower())

    return dict(categories)
