"""GTK lifecycle glue for Applications applet."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from docking.applets.applications.render import create_icon, make_menu_item_with_icon
from docking.applets.applications.state import CATEGORY_ICONS, _build_app_categories
from docking.applets.base import Applet
from docking.applets.identity import AppletId
from docking.i18n import _
from docking.log import get_logger, with_context

_log = with_context(
    get_logger(name="applications"),
    applet_id=str(AppletId.APPLICATIONS),
)


class ApplicationsApplet(Applet):
    """Categorized application launcher via right-click menu."""

    id = AppletId.APPLICATIONS
    name = _("Applications")
    icon_name = "view-app-grid"

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(size=size)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Build categorized app menu (lazy: scans on each open)."""
        items: list[Gtk.MenuItem] = []
        categories = _build_app_categories()

        for cat_name in sorted(categories.keys()):
            apps = categories[cat_name]
            if not apps:
                continue

            cat_item = make_menu_item_with_icon(
                label=cat_name,
                icon_name=CATEGORY_ICONS.get(cat_name),
            )
            submenu = Gtk.Menu()

            for app_info in apps:
                name = app_info.get_display_name() or "Unknown"
                menu_item = make_menu_item_with_icon(
                    label=name,
                    gicon=app_info.get_icon(),
                )
                menu_item.connect(
                    "activate",
                    lambda _, info=app_info: _launch_app(app_info=info),
                )
                submenu.append(menu_item)

            cat_item.set_submenu(submenu)
            items.append(cat_item)

        return items


def _launch_app(app_info: Gio.DesktopAppInfo) -> None:
    """Launch an application from its DesktopAppInfo."""
    desktop_id = app_info.get_id() if app_info else None
    try:
        app_info.launch([], None)
    except GLib.Error as exc:
        app_name = app_info.get_display_name() if app_info else None
        _log.bind(desktop_id=desktop_id, action="launch_app").warning(
            "Failed to launch application %s: %s",
            app_name,
            exc,
        )
