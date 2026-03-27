"""GTK lifecycle glue for Applications applet."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from docking.applets.applications import meta
from docking.applets.applications.render import create_icon, make_menu_item_with_icon
from docking.applets.applications.state import CATEGORY_ICONS, _build_app_categories
from docking.applets.base import Applet
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(
    get_logger(name="applications"),
    applet_id=meta.id,
)


class ApplicationsApplet(Applet):
    """Categorized application launcher via right-click menu."""

    id = meta.id
    name = _("Applications")
    icon_name = "view-app-grid"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(size=size)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Build categorized app menu (lazy: scans on each open)."""
        items: list[Gtk.MenuItem] = []
        categories = _build_app_categories()
        category_rows: list[
            tuple[Gtk.MenuItem, Gtk.Menu, list[Gio.DesktopAppInfo]]
        ] = []

        search_entry = Gtk.Entry()
        search_entry.set_placeholder_text(_("Search applications..."))
        search_entry.set_width_chars(24)
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        search_box.pack_start(search_entry, True, True, 0)
        search_item = Gtk.MenuItem()
        search_item.add(search_box)
        items.append(search_item)
        items.append(Gtk.SeparatorMenuItem())

        for cat_name in sorted(categories.keys()):
            apps = categories[cat_name]
            if not apps:
                continue

            cat_item = make_menu_item_with_icon(
                label=cat_name,
                icon_name=CATEGORY_ICONS.get(cat_name),
            )
            submenu = Gtk.Menu()

            _populate_app_submenu(submenu=submenu, apps=apps)

            cat_item.set_submenu(submenu)
            items.append(cat_item)
            category_rows.append((cat_item, submenu, apps))

        def apply_filter(query: str) -> None:
            lowered = query.strip().lower()
            for cat_item, submenu, apps in category_rows:
                matched = list(_filter_apps(apps=apps, query=lowered))
                _populate_app_submenu(submenu=submenu, apps=matched)
                if matched:
                    cat_item.show()
                else:
                    cat_item.hide()

        def on_search_changed(entry: Gtk.Entry) -> None:
            apply_filter(entry.get_text())

        def on_search_mapped(entry: Gtk.Entry, *_args: object) -> None:
            apply_filter(entry.get_text())
            entry.grab_focus()

        search_entry.connect("changed", on_search_changed)
        search_entry.connect("map", on_search_mapped)

        return items


def _app_name(app_info: Gio.DesktopAppInfo) -> str:
    return app_info.get_display_name() or "Unknown"


def _clear_menu(*, menu: Gtk.Menu) -> None:
    for child in list(menu.get_children()):
        menu.remove(child)


def _populate_app_submenu(
    *,
    submenu: Gtk.Menu,
    apps: Iterable[Gio.DesktopAppInfo],
) -> None:
    _clear_menu(menu=submenu)
    for app_info in apps:
        menu_item = make_menu_item_with_icon(
            label=_app_name(app_info),
            gicon=app_info.get_icon(),
        )
        menu_item.connect(
            "activate",
            lambda _, info=app_info: _launch_app(app_info=info),
        )
        submenu.append(menu_item)
    submenu.show_all()


def _filter_apps(
    *,
    apps: Iterable[Gio.DesktopAppInfo],
    query: str,
) -> Iterable[Gio.DesktopAppInfo]:
    if not query:
        return apps
    return [app for app in apps if query in _app_name(app).lower()]


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
