"""Applications applet -- categorized app launcher via right-click menu.

Scans installed .desktop files via Gio.AppInfo and groups them by
FreeDesktop category. No GMenu dependency required.
"""

from __future__ import annotations

from collections import defaultdict

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from docking.applets.base import Applet, load_theme_icon
from docking.applets.identity import AppletId

# FreeDesktop main categories -> display label
_CATEGORY_LABELS: dict[str, str] = {
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
_CATEGORY_ICONS: dict[str, str] = {
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
        display_cat = "Other"
        for raw_cat in cats.split(";"):
            if raw_cat in _CATEGORY_LABELS:
                display_cat = _CATEGORY_LABELS[raw_cat]
                break
        categories[display_cat].append(app_info)

    # Sort apps within each category by display name
    for apps in categories.values():
        apps.sort(key=lambda a: (a.get_display_name() or "").lower())

    return dict(categories)


class ApplicationsApplet(Applet):
    """Categorized application launcher via right-click menu."""

    id = AppletId.APPLICATIONS
    name = "Applications"
    icon_name = "view-app-grid"

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Static app grid icon."""
        return load_theme_icon(name="view-app-grid", size=size) or load_theme_icon(
            name="gnome-applications", size=size
        )

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Build categorized app menu (lazy: scans on each open)."""
        items: list[Gtk.MenuItem] = []
        categories = _build_app_categories()

        for cat_name in sorted(categories.keys()):
            apps = categories[cat_name]
            if not apps:
                continue

            cat_item = Gtk.ImageMenuItem(label=cat_name)
            cat_item.set_always_show_image(True)
            cat_icon = _CATEGORY_ICONS.get(cat_name)
            if cat_icon:
                cat_item.set_image(
                    Gtk.Image.new_from_icon_name(cat_icon, Gtk.IconSize.MENU)
                )
            submenu = Gtk.Menu()

            for app_info in apps:
                name = app_info.get_display_name() or "Unknown"
                mi = Gtk.ImageMenuItem(label=name)
                mi.set_always_show_image(True)
                gicon = app_info.get_icon()
                if gicon:
                    mi.set_image(Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.MENU))
                mi.connect(
                    "activate",
                    lambda _, info=app_info: _launch_app(app_info=info),
                )
                submenu.append(mi)

            cat_item.set_submenu(submenu)
            items.append(cat_item)

        return items


def _launch_app(app_info: Gio.DesktopAppInfo) -> None:
    """Launch an application from its DesktopAppInfo."""
    try:
        app_info.launch([], None)
    except GLib.Error:
        pass
