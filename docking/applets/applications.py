"""Applications applet -- categorized app launcher via right-click menu.

Scans installed .desktop files via Gio.AppInfo and groups them by
FreeDesktop category. No GMenu dependency required.
"""

from __future__ import annotations

from collections import defaultdict

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from docking.applets.base import Applet, load_theme_icon
from docking.applets.identity import AppletId
from docking.log import get_logger

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

_MENU_ICON_PX = 16
_log = get_logger(name="applications")


def _normalize_menu_icon(image: Gtk.Image) -> None:
    """Force consistent menu icon size across themes/environments."""
    image.set_pixel_size(_MENU_ICON_PX)
    image.set_size_request(_MENU_ICON_PX, _MENU_ICON_PX)
    image.set_valign(Gtk.Align.CENTER)


def _make_menu_item_with_icon(
    label: str,
    icon_name: str | None = None,
    gicon: Gio.Icon | None = None,
) -> Gtk.MenuItem:
    """Create a Gtk.MenuItem with an optional icon using non-deprecated widgets."""
    item = Gtk.MenuItem()
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    row.set_halign(Gtk.Align.START)
    row.set_margin_start(0)
    row.set_margin_end(0)

    image: Gtk.Image | None = None
    if gicon is not None:
        image = Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.MENU)
    elif icon_name:
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)

    if image is not None:
        _normalize_menu_icon(image)
        image.set_margin_start(0)
        image.set_margin_end(0)
        row.pack_start(image, False, False, 0)

    text = Gtk.Label(label=label)
    text.set_xalign(0.0)
    text.set_margin_start(0)
    row.pack_start(text, False, False, 0)

    item.add(row)
    return item


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

            cat_item = _make_menu_item_with_icon(
                label=cat_name, icon_name=_CATEGORY_ICONS.get(cat_name)
            )
            submenu = Gtk.Menu()

            for app_info in apps:
                name = app_info.get_display_name() or "Unknown"
                mi = _make_menu_item_with_icon(label=name, gicon=app_info.get_icon())
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
    except GLib.Error as exc:
        app_name = app_info.get_display_name() if app_info else None
        _log.warning("Failed to launch application %s: %s", app_name, exc)
