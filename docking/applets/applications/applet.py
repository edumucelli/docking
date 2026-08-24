# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""GTK lifecycle glue for Applications applet."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gtk

from docking.applets.applications import meta
from docking.applets.applications.render import create_icon, make_menu_item_with_icon
from docking.applets.applications.state import (
    CATEGORY_ICONS,
    build_app_categories,
)
from docking.applets.base import ApplicationServicesApplet
from docking.core.icons import IconSource
from docking.i18n import _
from docking.platform.applications.launcher import ApplicationLauncher
from docking.platform.applications.listing import (
    ApplicationListing,
    activate_listing,
    listing_desktop_file_uri,
    listing_gicon,
)
from docking.platform.applications.registry import ApplicationRegistry

if TYPE_CHECKING:
    from docking.core.config import Config

SEARCH_ENTRY_WIDTH_CHARS = 24
_URI_TARGET = Gtk.TargetEntry.new("text/uri-list", 0, 0)


class ApplicationsApplet(ApplicationServicesApplet):
    """Categorized application launcher via left-click menu."""

    id = meta.id
    name = _("Applications")
    icon_name = "view-app-grid"
    icon_source_options = (IconSource.DOCKING, IconSource.SYSTEM)

    def __init__(
        self,
        icon_size: int,
        config: Config,
        *,
        application_registry: ApplicationRegistry,
        application_launcher: ApplicationLauncher,
    ) -> None:
        super().__init__(
            icon_size=icon_size,
            config=config,
            application_registry=application_registry,
            application_launcher=application_launcher,
        )
        self._popup_menu: Gtk.Menu | None = None
        self.present()

    def create_docking_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(size=size)

    def on_clicked(self) -> None:
        """Show the categorized application menu on left-click."""
        menu = self._build_launcher_menu()
        if not menu.get_children():
            return

        self._popup_menu = menu

        def clear_popup(*_args: object) -> None:
            self._popup_menu = None

        menu.connect("hide", clear_popup)
        menu.connect("deactivate", clear_popup)
        menu.show_all()
        menu.popup_at_pointer(None)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Use left-click for the launcher menu; keep right-click minimal."""
        return []

    def _build_launcher_menu(self) -> Gtk.Menu:
        """Build the categorized launcher menu lazily on each open."""
        menu = Gtk.Menu()
        categories = build_app_categories(self._application_registry)
        category_rows: list[tuple[Gtk.MenuItem, list[ApplicationListing]]] = []

        search_entry = Gtk.Entry()
        search_entry.set_placeholder_text(_("Search applications..."))
        search_entry.set_width_chars(SEARCH_ENTRY_WIDTH_CHARS)
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        search_box.pack_start(search_entry, True, True, 0)
        search_item = Gtk.MenuItem()
        search_item.set_reserve_indicator(True)
        search_item.add(search_box)
        menu.append(search_item)
        menu.append(Gtk.SeparatorMenuItem())

        for cat_name in sorted(categories.keys()):
            apps = categories[cat_name]
            if not apps:
                continue

            cat_item = make_menu_item_with_icon(
                label=cat_name,
                icon_name=CATEGORY_ICONS.get(cat_name),
            )
            submenu = Gtk.Menu()

            _populate_app_submenu(
                submenu=submenu,
                apps=apps,
                config=self._config,
                registry=self._application_registry,
                launcher=self._application_launcher,
            )

            cat_item.set_submenu(submenu)
            menu.append(cat_item)
            category_rows.append((cat_item, apps))

        search_results: list[Gtk.MenuItem] = []

        def apply_filter(query: str) -> None:
            nonlocal search_results
            lowered = query.strip().lower()
            for result in search_results:
                menu.remove(result)
            search_results = []

            matches: list[ApplicationListing] = []
            for cat_item, apps in category_rows:
                if lowered:
                    cat_item.hide()
                    matches.extend(_filter_apps(apps=apps, query=lowered))
                else:
                    cat_item.show()

            for app_info in sorted(matches, key=lambda app: _app_name(app).lower()):
                result = _application_menu_item(
                    app_info=app_info,
                    config=self._config,
                    registry=self._application_registry,
                    launcher=self._application_launcher,
                )
                menu.append(result)
                result.show_all()
                search_results.append(result)

        def on_search_changed(entry: Gtk.Entry) -> None:
            apply_filter(entry.get_text())

        def on_search_mapped(entry: Gtk.Entry, *_args: object) -> None:
            apply_filter(entry.get_text())
            entry.grab_focus()

        search_entry.connect("changed", on_search_changed)
        search_entry.connect("map", on_search_mapped)

        return menu


def _app_name(app_info: ApplicationListing) -> str:
    return app_info.name or "Unknown"


def _clear_menu(*, menu: Gtk.Menu) -> None:
    for child in list(menu.get_children()):
        menu.remove(child)


def _populate_app_submenu(
    *,
    submenu: Gtk.Menu,
    apps: Iterable[ApplicationListing],
    config: Config,
    registry: ApplicationRegistry,
    launcher: ApplicationLauncher,
) -> None:
    _clear_menu(menu=submenu)
    for app_info in apps:
        submenu.append(
            _application_menu_item(
                app_info=app_info,
                config=config,
                registry=registry,
                launcher=launcher,
            )
        )
    submenu.show_all()


def _application_menu_item(
    *,
    app_info: ApplicationListing,
    config: Config,
    registry: ApplicationRegistry,
    launcher: ApplicationLauncher,
) -> Gtk.MenuItem:
    menu_item = make_menu_item_with_icon(
        label=_app_name(app_info),
        gicon=listing_gicon(registry, app_info),
    )
    menu_item.connect(
        "activate",
        lambda _, info=app_info: _launch_app(
            app_info=info,
            launcher=launcher,
        ),
    )
    _configure_drag_source(
        menu_item=menu_item,
        app_info=app_info,
        config=config,
        registry=registry,
    )
    return menu_item


def _configure_drag_source(
    *,
    menu_item: Gtk.MenuItem,
    app_info: ApplicationListing,
    config: Config,
    registry: ApplicationRegistry,
) -> None:
    """Make a launchable menu row draggable to the dock as a desktop URI."""
    if config.lock_icons:
        return
    uri = listing_desktop_file_uri(app_info)
    if uri is None:
        return
    menu_item.drag_source_set(
        Gdk.ModifierType.BUTTON1_MASK,
        [_URI_TARGET],
        Gdk.DragAction.COPY,
    )
    menu_item.connect("drag-begin", _on_drag_begin, app_info, registry)
    menu_item.connect("drag-data-get", _on_drag_data_get, uri)


def _on_drag_begin(
    _menu_item: Gtk.MenuItem,
    context: Gdk.DragContext,
    app_info: ApplicationListing,
    registry: ApplicationRegistry,
) -> None:
    """Show the application's icon beside the pointer while it is dragged."""
    icon = listing_gicon(registry, app_info)
    if icon is not None:
        Gtk.drag_set_icon_gicon(context, icon, 0, 0)


def _on_drag_data_get(
    _menu_item: Gtk.MenuItem,
    _context: Gdk.DragContext,
    selection: Gtk.SelectionData,
    _info: int,
    _time: int,
    uri: str,
) -> None:
    """Provide the selected application's desktop-entry URI to the dock."""
    selection.set_uris([uri])


def _filter_apps(
    *,
    apps: Iterable[ApplicationListing],
    query: str,
) -> Iterable[ApplicationListing]:
    if not query:
        return apps
    return [app for app in apps if query in _app_name(app).lower()]


def _launch_app(
    *,
    app_info: ApplicationListing,
    launcher: ApplicationLauncher,
) -> bool:
    """Launch a registry listing through the injected application launcher."""
    return activate_listing(launcher, app_info)
