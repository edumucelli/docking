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

"""Context menu construction for dock items, applets, folders, and background.

Why the dock menu logic is centralized

Right-click behavior in a dock is deceptively broad. Depending on where the
pointer is, the same action can mean:

- item menu for an application launcher,
- applet menu with applet-specific actions,
- folder item menu,
- dock background menu,
- live menu for currently open application windows.

If each item type built its own menus independently, the dock would lose
consistency in:

- popup lifecycle,
- autohide/menu interaction,
- item targeting,
- icon/title formatting,
- shared commands like pin/unpin, lock positions, theme changes, and position.

This module is the centralized menu builder for those cases.

What this module owns

MenuHandler owns:

- deciding whether a right-click targets an item or the dock background,
- constructing GTK menu trees,
- building item-specific and dock-specific actions,
- applet submenu organization,
- folder item menus,
- live window menu entries with backend-neutral window actions,
- popup creation and lifecycle hookup.

It does not own:

- dock geometry,
- autohide policy directly,
- tooltip/preview lifecycle directly,
- actual runtime side effects on the dock shell.

Those imperative side effects are routed through `DockRuntime`.

Why geometry matters for menus

The dock must support this state:

    pointer inside dock
      but
    pointer not on any item

That is what makes the dock background menu reachable.

So menu targeting follows shared geometry:

    event point
      |
      +--> item_at_point(...) ? ---- yes --> build item/applet/folder menu
      |
      +--> no -----------------------> build dock background menu

This is one of the reasons click/hit geometry is intentionally narrower than
hover geometry. The background needs to remain a real target.

Runtime command boundary

This module is intentionally not allowed to mutate DockWindow internals freely.
The important split is:

- MenuHandler
  decides what commands exist and when they are offered

- DockRuntime
  performs dock-wide side effects such as:
    - menu popup open/close hooks,
    - reposition,
    - strut updates,
    - redraws,
    - active-display toggles,
    - hover UI cleanup

That boundary matters because menu code is broad enough already; it should not
also become the place where raw window internals are orchestrated directly.

Kinds of menus built here

1. Application item menu
   Launch, pin/unpin, close windows, desktop actions, etc.

2. Applet menu
   Applet-specific commands and applet insertion choices.

3. Folder item menu
   A right-click directory view with sorting/filtering behavior.

4. Dock background menu
   Global dock behavior such as:
   - position
   - autohide
   - icon options
   - theme selection
   - applet insertion
   - quit/about

Left-click folder stacks are coordinated through `FolderStackController`; this
module only exposes the facade methods used by dock input handling.

Window entries in menus

For running applications, the menu may include live window entries. Those rows
use backend-neutral window snapshots so activation and close actions do not need
raw Wnck windows or XIDs in the menu layer.

Popup lifecycle

Menu popups affect dock behavior even though they are not part of the dock
window itself:

    menu opens
      |
      +--> runtime.menu_popup_opened()
      |
      +--> autohide disabled while menu is active

    menu hides
      |
      +--> runtime.menu_popup_closed()
      |
      +--> interaction policy re-evaluates pointer position

That lifecycle is why menu creation and menu popup hookup are not separate
concerns in practice.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from docking.applets import get_applet_catalog
from docking.applets.base import (
    ICON_SOURCE_DOCKING,
    ICON_SOURCE_SYSTEM,
    Applet,
    load_catalog_icon,
)
from docking.applets.identity import (
    APPLET_CATEGORY_ORDER,
    AppletCategory,
    AppletMeta,
    applet_desktop_id,
)
from docking.applets.identity import is_applet_desktop_id as is_applet
from docking.applets.separator import meta as _separator_meta
from docking.core.config import WindowListSort
from docking.core.icons import IconSource, icon_source_from_value
from docking.core.items import FILE_KIND, FOLDER_KIND
from docking.i18n import _
from docking.log import get_logger
from docking.platform import icon_overrides
from docking.platform.applications.recent_documents import recent_documents_for
from docking.platform.backends.base import DisplayServer
from docking.ui.about import AboutDialogController
from docking.ui.diagnostics import DiagnosticsDialogController
from docking.ui.folder.stack import FolderStackController
from docking.ui.geometry import DockGeometryFrame
from docking.ui.runtime import DockRuntime
from docking.ui.settings import SettingsWindowController

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem
    from docking.platform.applications.launcher import ApplicationLauncher
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.backends.base import (
        PreviewService,
        WindowId,
        WindowService,
        WindowSnapshot,
    )
    from docking.platform.model import DockModel
    from docking.platform.targets import TargetService
    from docking.search.presenter import SearchPresenter


APPLET_MENU_ICON_PX = 16
MENU_LABEL_MAX_CHARS = 32
MENU_ROW_SPACING_PX = 6
WINDOW_MENU_THUMB_W = 28
WINDOW_MENU_THUMB_H = 20
WINDOW_MENU_CLOSE_HIT_W = 44
WINDOW_MENU_CLOSE_LABEL_XALIGN = 0.5
WINDOW_MENU_CLOSE_MARGIN_END_PX = 12
FOLDER_MENU_REFRESH_DEBOUNCE_MS = 120
log = get_logger("menu")

T = TypeVar("T")

SUPPORT_URL = "https://github.com/edumucelli/docking/issues"


def _make_menu_header(label: str) -> Gtk.MenuItem:
    item = Gtk.MenuItem(label=label)
    item.set_sensitive(False)
    return item


def _build_radio_submenu(
    label: str,
    items: Sequence[tuple[str, T]],
    current: T,
    on_changed: Callable[[Gtk.RadioMenuItem, T], None],
) -> Gtk.MenuItem:
    """Build a MenuItem with a radio-group submenu.

    Args:
        label: Submenu parent label
        items: [(display_text, value), ...] for each radio option
        current: Currently active value (compared with ==)
        on_changed: Callback(widget, value) connected to "activate"
    """
    menu_item = Gtk.MenuItem(label=label)
    submenu = Gtk.Menu()
    first: Gtk.RadioMenuItem | None = None
    for display, value in items:
        radio = Gtk.RadioMenuItem(label=display)
        if first:
            radio.join_group(first)
        else:
            first = radio
        if value == current:
            radio.set_active(True)
        radio.connect("activate", on_changed, value)
        submenu.append(radio)
    menu_item.set_submenu(submenu)
    return menu_item


def _set_menu_item_icon(
    *,
    item: Gtk.MenuItem,
    label: str,
    pixbuf: GdkPixbuf.Pixbuf | None,
    icon_px: int,
) -> None:
    item.set_label(label)
    row = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=MENU_ROW_SPACING_PX,
    )
    if pixbuf is not None:
        scaled = pixbuf
        if pixbuf.get_width() != icon_px or pixbuf.get_height() != icon_px:
            scaled = pixbuf.scale_simple(
                icon_px, icon_px, GdkPixbuf.InterpType.BILINEAR
            )
        image = Gtk.Image.new_from_pixbuf(scaled or pixbuf)
        image.set_pixel_size(icon_px)
        row.pack_start(image, False, False, 0)

    text = Gtk.Label(label=label)
    text.set_xalign(0.0)
    text.set_max_width_chars(MENU_LABEL_MAX_CHARS)
    text.set_ellipsize(Pango.EllipsizeMode.END)
    text.set_single_line_mode(True)
    row.pack_start(text, False, False, 0)

    child = item.get_child()
    if child is not None:
        item.remove(child)
    item.add(row)


class MenuHandler:
    """Builds and shows context menus for dock items."""

    def __init__(
        self,
        about: AboutDialogController,
        settings: SettingsWindowController,
        runtime: DockRuntime,
        model: DockModel,
        config: Config,
        window_tracker: WindowService,
        preview_service: PreviewService,
        folder_stack: FolderStackController,
        diagnostics: DiagnosticsDialogController,
        dock_window: Gtk.Window,
        search: SearchPresenter,
        application_registry: ApplicationRegistry,
        application_launcher: ApplicationLauncher,
        target_service: TargetService,
    ) -> None:
        self._about = about
        self._settings = settings
        self._diagnostics = diagnostics
        self._runtime = runtime
        self._model = model
        self._config = config
        self._tracker = window_tracker
        self._preview_service = preview_service
        self._dock_window = dock_window
        self._search = search
        self._application_registry = application_registry
        self._application_launcher = application_launcher
        self._target_service = target_service
        self._folder_stack = folder_stack
        self._folder_menu_monitors: dict[int, Gio.FileMonitor] = {}
        self._folder_menu_context: dict[int, tuple[Gtk.Menu, DockItem, str, bool]] = {}
        self._folder_menu_refresh_sources: dict[int, int] = {}
        self._folder_menu_signal_connected: set[int] = set()

    def show(
        self,
        event: Gdk.EventButton,
        cursor_main: float,
        *,
        frame: DockGeometryFrame,
        force_background: bool = False,
    ) -> None:
        """Build and show the right-click context menu.

        Hit-tests the cursor to determine whether to show an item-specific
        menu (desktop actions, pin/unpin, close) or a dock background menu
        (autohide, theme, position, applets, quit). When *force_background*
        is true, the background menu is shown even if the pointer is over an
        item; this makes the global dock menu reachable from any shelf point.
        """
        item = None if force_background else frame.item_at_point(event.x, event.y)

        if item:
            menu = self._new_popup_menu()
            self._build_item_menu(menu=menu, item=item)
        else:
            menu = self._new_popup_menu()
            insert_idx = self._insert_index(cursor_main=cursor_main, frame=frame)
            self._build_dock_menu(menu=menu, insert_index=insert_idx)

        menu.show_all()
        menu.popup_at_pointer(event)

    def _invalidate_folder_target_cache(self, target: str) -> None:
        self._folder_stack.invalidate_target(target)

    def _new_popup_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()
        self._runtime.menu_popup_opened()
        menu.connect("hide", self._on_menu_popup_closed)
        menu.connect("deactivate", self._on_menu_popup_closed)
        return menu

    def _on_menu_popup_closed(self, menu: Gtk.Menu) -> None:
        self._cleanup_folder_menu_tree(menu)
        self._runtime.menu_popup_closed()

    @staticmethod
    def _applet_icon_source_options(applet: Applet) -> tuple[IconSource, ...]:
        declared = getattr(applet, "icon_source_options", None)
        if isinstance(declared, tuple | list):
            options = tuple(
                source
                for source in (icon_source_from_value(value) for value in declared)
                if source is not None
            )
            if options:
                return tuple(dict.fromkeys(options))
        return (IconSource.DOCKING,)

    def _build_applet_icon_source_menu(self, applet: Applet) -> Gtk.MenuItem:
        labels = {
            ICON_SOURCE_DOCKING: _("Docking Icon"),
            ICON_SOURCE_SYSTEM: _("System Icon"),
        }
        items = tuple(
            (labels[source.value], source.value)
            for source in self._applet_icon_source_options(applet=applet)
            if source.value in labels
        )
        return _build_radio_submenu(
            label=_("Icon"),
            items=items,
            current=applet.icon_source(),
            on_changed=lambda widget, source: self._on_applet_icon_source_changed(
                widget, applet, source
            ),
        )

    def _build_icon_menu(self, item: DockItem) -> Gtk.MenuItem:
        menu_item = Gtk.MenuItem(label=_("Icon"))
        submenu = Gtk.Menu()

        default_item = Gtk.MenuItem(label=_("Default Icon"))
        default_item.connect("activate", lambda _: self._model.reset_custom_icon(item))
        submenu.append(default_item)

        choose_item = Gtk.MenuItem(label=_("Choose From File..."))
        choose_item.connect("activate", lambda _: self._on_choose_custom_icon(item))
        submenu.append(choose_item)

        reset_item = Gtk.MenuItem(label=_("Reset Custom Icon"))
        reset_item.set_sensitive(
            icon_overrides.custom_icon_path(config=self._config, item=item) is not None
        )
        reset_item.connect("activate", lambda _: self._model.reset_custom_icon(item))
        submenu.append(reset_item)

        menu_item.set_submenu(submenu)
        return menu_item

    def _on_choose_custom_icon(self, item: DockItem) -> None:
        path = self._choose_icon_file()
        if path is None:
            return
        if not self._model.set_custom_icon(item=item, path=path):
            self._show_icon_error(path=path)

    def _choose_icon_file(self) -> Path | None:
        dialog = Gtk.FileChooserDialog(
            title=_("Choose Icon"),
            transient_for=self._dock_window,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            _("Cancel"),
            Gtk.ResponseType.CANCEL,
            _("Open"),
            Gtk.ResponseType.OK,
        )
        self._add_icon_file_filters(dialog=dialog)
        try:
            response = dialog.run()
            if response != Gtk.ResponseType.OK:
                return None
            filename = dialog.get_filename()
            return Path(filename).expanduser() if filename else None
        finally:
            dialog.destroy()

    @staticmethod
    def _add_icon_file_filters(dialog: Gtk.FileChooserDialog) -> None:
        image_filter = Gtk.FileFilter()
        image_filter.set_name(_("Images"))
        for mime_type in ("image/png", "image/svg+xml", "image/x-xpixmap"):
            image_filter.add_mime_type(mime_type)
        for pattern in ("*.png", "*.svg", "*.xpm"):
            image_filter.add_pattern(pattern)
        dialog.add_filter(image_filter)

    def _show_icon_error(self, *, path: Path) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self._dock_window,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=_("Could not use selected icon"),
        )
        dialog.format_secondary_text(str(path))
        try:
            dialog.run()
        finally:
            dialog.destroy()

    def _on_applet_icon_source_changed(
        self,
        widget: Gtk.RadioMenuItem,
        applet: Applet,
        source: str,
    ) -> None:
        if not widget.get_active():
            return
        applet.set_icon_source(source)

    def _build_item_menu(self, menu: Gtk.Menu, item: DockItem) -> None:
        """Build context menu for a specific dock item.

        Applets: applet actions, optional icon source, and "Remove from Dock".
        Regular items: desktop actions (quicklists), pin/unpin, close.
        """
        locked = self._config.lock_icons

        if is_applet(desktop_id=item.desktop_id):
            # Applet-specific menu items
            applet = self._model.get_applet(item.desktop_id)
            applet_items: list[Gtk.MenuItem] = []
            has_icon_source = False
            if applet:
                applet_items = applet.get_menu_items()
                has_icon_source = len(self._applet_icon_source_options(applet)) > 1
                for mi in applet_items:
                    menu.append(mi)
                if applet_items and has_icon_source:
                    menu.append(Gtk.SeparatorMenuItem())
                if has_icon_source:
                    menu.append(self._build_applet_icon_source_menu(applet))
            if not locked:
                if applet_items or has_icon_source:
                    menu.append(Gtk.SeparatorMenuItem())
                remove = Gtk.MenuItem(label=_("Remove from Dock"))
                remove.connect(
                    "activate",
                    lambda _: self._model.remove_applet(item.desktop_id),
                )
                menu.append(remove)
            return

        if item.kind == FOLDER_KIND:
            self._build_folder_item_menu(menu=menu, item=item)
            return

        if item.kind == FILE_KIND:
            open_item = Gtk.MenuItem(label=_("Open"))
            open_item.connect(
                "activate", lambda _: self._target_service.open_target(item.target)
            )
            menu.append(open_item)
            if item.is_pinned:
                menu.append(self._build_icon_menu(item))
            if not locked:
                menu.append(Gtk.SeparatorMenuItem())
                remove = Gtk.MenuItem(label=_("Remove from Dock"))
                remove.connect(
                    "activate", lambda _: self._model.unpin_item(item.desktop_id)
                )
                menu.append(remove)
            return

        # Desktop actions (e.g. "New Window", "New Incognito Window")
        self._append_desktop_actions(menu=menu, desktop_id=item.desktop_id)

        # Recent Documents (per-app jumplist)
        if self._config.show_recent_docs_in_menu:
            self._append_recent_docs(menu=menu, desktop_id=item.desktop_id)

        # Open windows - click to activate
        self._append_open_windows(menu=menu, desktop_id=item.desktop_id)

        if item.is_pinned:
            menu.append(self._build_icon_menu(item))

        # Pin/Unpin (hidden when icons are locked)
        if not locked:
            if item.is_pinned:
                unpin = Gtk.MenuItem(label=_("Remove from Dock"))
                unpin.connect(
                    "activate",
                    lambda _: self._model.unpin_item(item.desktop_id),
                )
                menu.append(unpin)
            else:
                pin = Gtk.MenuItem(label=_("Keep in Dock"))
                pin.connect(
                    "activate",
                    lambda _: self._model.pin_item(item.desktop_id),
                )
                menu.append(pin)

        if item.is_running and item.instance_count > 0:
            menu.append(Gtk.SeparatorMenuItem())
            label = _("Close All") if item.instance_count > 1 else _("Close")
            close = Gtk.MenuItem(label=label)
            close.connect(
                "activate",
                lambda _: self._tracker.close_all(item.desktop_id),
            )
            menu.append(close)

    def _build_folder_item_menu(self, menu: Gtk.Menu, item: DockItem) -> None:
        self._track_folder_menu(
            menu=menu, folder_item=item, target=item.target, is_root=True
        )
        self._populate_directory_menu(menu=menu, folder_item=item, target=item.target)
        menu.append(Gtk.SeparatorMenuItem())
        prefs = self._folder_stack.folder_prefs(item)
        menu.append(
            _build_radio_submenu(
                label=_("Sort By"),
                items=self._folder_stack.sort_options(),
                current=prefs["sort"],
                on_changed=lambda widget, value, folder=item: (
                    self._on_folder_sort_changed(widget, folder, value)
                ),
            )
        )
        hidden = Gtk.CheckMenuItem(label=_("Show Hidden Files"))
        hidden.set_active(bool(prefs["show_hidden"]))
        hidden.connect("toggled", self._on_folder_hidden_toggled, item)
        menu.append(hidden)

        if item.is_pinned:
            menu.append(self._build_icon_menu(item))

        if not self._config.lock_icons:
            menu.append(Gtk.SeparatorMenuItem())
            remove = Gtk.MenuItem(label=_("Remove from Dock"))
            remove.connect(
                "activate", lambda _: self._model.unpin_item(item.desktop_id)
            )
            menu.append(remove)

    def _insert_index(
        self,
        cursor_main: float,
        frame: DockGeometryFrame,
    ) -> int:
        """Compute pinned insertion index from cursor position."""
        return frame.insertion_index_for_main(cursor_main, pos=self._config.pos)

    def _build_dock_menu(self, menu: Gtk.Menu, insert_index: int = -1) -> None:
        """Build context menu for the dock background (no item under cursor).

        Sections: add actions plus preferences/about/quit.
        """
        search_item = Gtk.MenuItem(label=_("Search..."))
        search_item.connect("activate", lambda _: self._search.show())
        menu.append(search_item)
        menu.append(Gtk.SeparatorMenuItem())

        # Add Applet submenu
        try:
            catalog = get_applet_catalog()
        except Exception as exc:
            log.warning("Failed to read applet catalog for add-applet menu: %s", exc)
            catalog = {}
        active_ids = {
            item.desktop_id
            for item in self._model.pinned_items
            if is_applet(desktop_id=item.desktop_id)
        }
        add_applet = Gtk.MenuItem(label=_("Add Applet"))
        add_applet_menu = Gtk.Menu()
        grouped: dict[AppletCategory, list[tuple[str, AppletMeta]]] = {
            category: [] for category in APPLET_CATEGORY_ORDER
        }
        for did, entry in sorted(catalog.items(), key=lambda item: str(item[0])):
            if did == _separator_meta.id:
                continue
            desktop_id = applet_desktop_id(applet_id=did)
            if desktop_id in active_ids:
                continue
            grouped[entry.category].append((did, entry))

        non_empty_categories = [
            key for key in APPLET_CATEGORY_ORDER if grouped.get(key)
        ]
        if non_empty_categories:
            for category in non_empty_categories:
                category_item = Gtk.MenuItem(label=_(category.value))
                category_menu = Gtk.Menu()
                for did, entry in sorted(
                    grouped[category], key=lambda item: item[1].name.lower()
                ):
                    item = Gtk.MenuItem(label=entry.name)
                    pixbuf: GdkPixbuf.Pixbuf | None = load_catalog_icon(
                        applet_id=did,
                        size=APPLET_MENU_ICON_PX,
                    )
                    _set_menu_item_icon(
                        item=item,
                        label=entry.name,
                        pixbuf=pixbuf,
                        icon_px=APPLET_MENU_ICON_PX,
                    )
                    item.connect("activate", self._on_add_applet_activate, str(did))
                    category_menu.append(item)
                category_item.set_submenu(category_menu)
                add_applet_menu.append(category_item)
        else:
            empty = Gtk.MenuItem(label=_("No Applets Available"))
            empty.set_sensitive(False)
            add_applet_menu.append(empty)
        add_applet.set_submenu(add_applet_menu)
        menu.append(add_applet)

        # Add Separator (multi-instance, not a toggle)
        add_sep = Gtk.MenuItem(label=_("Add Separator"))
        add_sep.connect(
            "activate",
            lambda _, idx=insert_index: self._model.add_separator(index=idx),
        )
        menu.append(add_sep)

        menu.append(Gtk.SeparatorMenuItem())

        diagnostics_item = Gtk.MenuItem(label=_("Diagnostics"))
        diagnostics_item.connect("activate", lambda _: self._diagnostics.show())
        menu.append(diagnostics_item)

        # Preferences
        prefs_item = Gtk.MenuItem(label=_("Preferences"))
        prefs_item.connect("activate", lambda _: self._settings.show())
        menu.append(prefs_item)

        # About
        about_item = Gtk.MenuItem(label=_("About"))
        about_item.connect("activate", lambda _: self._about.show())
        menu.append(about_item)

        # Support
        support_item = Gtk.MenuItem(label=_("Get Support"))
        support_item.connect(
            "activate", lambda _: self._target_service.open_target(SUPPORT_URL)
        )
        menu.append(support_item)

        # Quit
        quit_item = Gtk.MenuItem(label=_("Quit"))
        quit_item.connect("activate", lambda _: self._runtime.quit())
        menu.append(quit_item)

    def _append_desktop_actions(self, menu: Gtk.Menu, desktop_id: str) -> None:
        """Append desktop actions (quicklists) from .desktop file, if any."""
        actions = self._application_launcher.quicklist_actions(desktop_id)
        if not actions:
            return
        for action in actions:
            mi = Gtk.MenuItem(label=action.name)
            # Capture by value via default arg
            mi.connect(
                "activate",
                lambda _, did=desktop_id, aid=action.action_id: (
                    self._application_launcher.launch_action(did, aid)
                ),
            )
            menu.append(mi)
        menu.append(Gtk.SeparatorMenuItem())

    def _append_recent_docs(self, menu: Gtk.Menu, desktop_id: str) -> None:
        """Append a "Recent Documents" submenu for apps with recent file history."""
        application = self._application_registry.get(desktop_id)
        if application is None:
            return
        docs = recent_documents_for(
            application,
            limit=self._config.recent_docs_max,
        )
        if not docs:
            return

        submenu = Gtk.Menu()
        for doc in docs:
            row = Gtk.MenuItem()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            try:
                recent_item = Gtk.RecentManager.get_default().lookup_item(doc.uri)
                if recent_item is not None:
                    gicon = recent_item.get_gicon()
                    if gicon is not None:
                        icon = Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.MENU)
                        box.pack_start(icon, False, False, 0)
            except Exception:
                pass
            name = Gtk.Label(label=doc.name, xalign=0)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            name.set_max_width_chars(30)
            box.pack_start(name, True, True, 0)
            closed_dt = dt.datetime.fromtimestamp(doc.modified, tz=dt.timezone.utc)
            from docking.ui.tooltip import relative_time_label

            rel = relative_time_label(closed_dt)
            ts = Gtk.Label(label=rel, xalign=1)
            ts.set_sensitive(False)
            box.pack_start(ts, False, False, 0)
            row.add(box)
            uri = doc.uri
            row.connect(
                "activate",
                lambda _, u=uri: self._target_service.open_target(u),
            )
            submenu.append(row)

        submenu.append(Gtk.SeparatorMenuItem())
        clear = Gtk.MenuItem(label=_("Clear Recent Documents"))
        recents = Gtk.RecentManager.get_default()
        clear.connect(
            "activate",
            lambda _, rm=recents, uris=[d.uri for d in docs]: (
                [rm.remove_item(u) for u in uris]
            ),
        )
        submenu.append(clear)

        item = Gtk.MenuItem(label=_("Recent Documents"))
        item.set_submenu(submenu)
        menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())

    def _append_open_windows(self, menu: Gtk.Menu, desktop_id: str) -> None:
        """Append running windows as rich menu rows with activate/close."""
        windows = list(self._tracker.list_windows(desktop_id=desktop_id))
        if not windows:
            return
        if self._config.window_list_sort == WindowListSort.ALPHABETICAL.value:
            windows = sorted(windows, key=lambda window: window.title.lower())
        for window in windows:
            menu.append(self._build_window_menu_row(window=window))
        separator = Gtk.SeparatorMenuItem()
        separator._window_rows_separator = True
        menu.append(separator)

    def _build_window_menu_row(self, window: WindowSnapshot) -> Gtk.MenuItem:
        title = window.title or _("Window")
        row = Gtk.MenuItem()
        row.set_label(title)

        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=MENU_ROW_SPACING_PX,
        )
        thumbnail = self._preview_service.thumbnail(
            window.id,
            width=WINDOW_MENU_THUMB_W,
            height=WINDOW_MENU_THUMB_H,
        )
        image = (
            Gtk.Image.new_from_pixbuf(cast(GdkPixbuf.Pixbuf, thumbnail.image))
            if thumbnail is not None
            else Gtk.Image()
        )
        image.set_pixel_size(WINDOW_MENU_THUMB_H)
        box.pack_start(image, False, False, 0)

        text = Gtk.Label(label=title)
        text.set_xalign(0.0)
        text.set_max_width_chars(MENU_LABEL_MAX_CHARS)
        text.set_ellipsize(Pango.EllipsizeMode.END)
        text.set_single_line_mode(True)
        text.set_hexpand(True)
        box.pack_start(text, True, True, 0)

        close_label = Gtk.Label(label="\u00d7")
        close_label.set_xalign(WINDOW_MENU_CLOSE_LABEL_XALIGN)
        close_label.set_margin_end(WINDOW_MENU_CLOSE_MARGIN_END_PX)
        box.pack_end(close_label, False, False, 0)

        child = row.get_child()
        if child is not None:
            row.remove(child)
        row.add(box)
        row._window_row = True
        row.connect("button-press-event", self._on_window_row_button_press, window.id)
        row.connect(
            "button-release-event", self._on_window_row_button_release, window.id
        )
        row.connect("activate", lambda *_a: self._tracker.activate(window.id))
        return row

    def _window_close_zone_hit(
        self, widget: Gtk.Widget, event: Gdk.EventButton
    ) -> bool:
        x = float(event.x)
        if x < 0:
            return False
        alloc = widget.get_allocation()
        width = float(alloc.width)
        return width > 0 and x >= max(0.0, width - WINDOW_MENU_CLOSE_HIT_W)

    def _on_window_row_button_press(
        self, widget: Gtk.Widget, event: Gdk.EventButton, window_id: WindowId
    ) -> bool:
        return self._window_close_zone_hit(widget=widget, event=event)

    def _on_window_row_button_release(
        self, widget: Gtk.Widget, event: Gdk.EventButton, window_id: WindowId
    ) -> bool:
        if not self._window_close_zone_hit(widget=widget, event=event):
            return False
        self._tracker.close(window_id)
        if window_id.backend is DisplayServer.WAYLAND:
            GLib.idle_add(self._remove_window_row_deferred, widget, event)
        else:
            self._remove_window_row(widget=widget, event=event)
        self._runtime.hide_hover_ui()
        return True

    def _remove_window_row_deferred(
        self, widget: Gtk.Widget, event: Gdk.EventButton | None = None
    ) -> bool:
        self._remove_window_row(widget=widget, event=event, refresh_popup=False)
        return False

    def _remove_window_row(
        self,
        widget: Gtk.Widget,
        event: Gdk.EventButton | None = None,
        refresh_popup: bool = True,
    ) -> None:
        parent = widget.get_parent()
        if parent is None or not isinstance(parent, Gtk.Menu):
            return
        widget.hide()
        parent.remove(widget)
        widget.destroy()
        children = list(parent.get_children())
        if not any(getattr(child, "_window_row", False) for child in children):
            for child in children:
                if getattr(child, "_window_rows_separator", False):
                    child.hide()
                    parent.remove(child)
                    child.destroy()
                    break
        if refresh_popup:
            parent.popdown()
        parent.show_all()
        parent.queue_resize()
        parent.check_resize()
        parent.queue_draw()
        if refresh_popup:
            parent.popup_at_pointer(event)

    def _on_add_applet_activate(self, _widget: Gtk.MenuItem, applet_id: str) -> None:
        self._model.add_applet(applet_id)

    def _populate_directory_menu(
        self, menu: Gtk.Menu, folder_item: DockItem, target: str
    ) -> None:
        rows = self._folder_stack.list_directory(folder_item=folder_item, target=target)
        for child in rows:
            self._append_directory_row(menu=menu, folder_item=folder_item, child=child)

    def _append_directory_row(
        self, menu: Gtk.Menu, folder_item: DockItem, child: dict[str, Any]
    ) -> None:
        row = Gtk.MenuItem(label=child["name"])
        _set_menu_item_icon(
            item=row,
            label=child["name"],
            pixbuf=child["icon"],
            icon_px=self._folder_stack.icon_px(folder_item=folder_item),
        )
        if child["is_dir"]:
            if not child.get("has_children", False):
                row.connect(
                    "activate",
                    lambda _, child_target=child["target"]: (
                        self._target_service.open_target(child_target)
                    ),
                )
                menu.append(row)
                return
            submenu = Gtk.Menu()
            submenu.connect(
                "show",
                self._on_folder_submenu_show,
                folder_item,
                child["target"],
            )
            row.set_submenu(submenu)
        else:
            row.connect(
                "activate",
                lambda _, child_target=child["target"]: (
                    self._target_service.open_target(child_target)
                ),
            )
        menu.append(row)

    def _on_folder_submenu_show(
        self, menu: Gtk.Menu, folder_item: DockItem, target: str
    ) -> None:
        self._track_folder_menu(
            menu=menu, folder_item=folder_item, target=target, is_root=False
        )
        if menu.get_children():
            return
        self._populate_directory_menu(menu=menu, folder_item=folder_item, target=target)
        menu.show_all()

    def _track_folder_menu(
        self, menu: Gtk.Menu, folder_item: DockItem, target: str, is_root: bool
    ) -> None:
        menu_id = id(menu)
        self._folder_menu_context[menu_id] = (menu, folder_item, target, is_root)
        if menu_id not in self._folder_menu_signal_connected:
            menu.connect("hide", self._on_folder_menu_hidden)
            self._folder_menu_signal_connected.add(menu_id)

        if menu_id in self._folder_menu_monitors:
            return

        uri = self._target_service.normalize_file_target(target)
        if uri is None:
            return
        try:
            folder = Gio.File.new_for_uri(uri)
            monitor = folder.monitor_directory(Gio.FileMonitorFlags.NONE, None)
            monitor.connect("changed", self._on_folder_menu_changed, menu_id)
            self._folder_menu_monitors[menu_id] = monitor
        except GLib.Error as exc:
            log.warning("Failed to monitor folder menu target %s: %s", target, exc)
            return

    def _on_folder_menu_hidden(self, menu: Gtk.Menu) -> None:
        self._cleanup_folder_menu_tree(menu)

    def _on_folder_menu_changed(
        self,
        _monitor: Gio.FileMonitor,
        _file: Gio.File,
        _other_file: Gio.File | None,
        _event_type: Gio.FileMonitorEvent,
        menu_id: int,
    ) -> None:
        context = self._folder_menu_context.get(menu_id)
        if context is not None:
            _menu, _folder_item, target, _is_root = context
            self._invalidate_folder_target_cache(target)
        existing = self._folder_menu_refresh_sources.pop(menu_id, 0)
        if existing:
            GLib.source_remove(existing)
        source = GLib.timeout_add(
            FOLDER_MENU_REFRESH_DEBOUNCE_MS,
            self._refresh_folder_menu,
            menu_id,
        )
        self._folder_menu_refresh_sources[menu_id] = source

    def _refresh_folder_menu(self, menu_id: int) -> bool:
        self._folder_menu_refresh_sources.pop(menu_id, None)
        context = self._folder_menu_context.get(menu_id)
        if context is None:
            return False
        menu, folder_item, target, is_root = context
        self._clear_menu_children(menu)
        if is_root:
            self._build_folder_item_menu(menu=menu, item=folder_item)
        else:
            self._populate_directory_menu(
                menu=menu, folder_item=folder_item, target=target
            )
        menu.show_all()
        return False

    def _clear_menu_children(self, menu: Gtk.Menu) -> None:
        for child in list(menu.get_children()):
            submenu = child.get_submenu() if isinstance(child, Gtk.MenuItem) else None
            if isinstance(submenu, Gtk.Menu):
                self._cleanup_folder_menu_tree(submenu)
            menu.remove(child)

    def _cleanup_folder_menu_tree(self, menu: Gtk.Menu) -> None:
        for child in list(menu.get_children()):
            submenu = child.get_submenu() if isinstance(child, Gtk.MenuItem) else None
            if isinstance(submenu, Gtk.Menu):
                self._cleanup_folder_menu_tree(submenu)
        self._cleanup_folder_menu(menu)

    def _cleanup_folder_menu(self, menu: Gtk.Menu) -> None:
        menu_id = id(menu)
        refresh_source = self._folder_menu_refresh_sources.pop(menu_id, 0)
        if refresh_source:
            GLib.source_remove(refresh_source)
        monitor = self._folder_menu_monitors.pop(menu_id, None)
        if monitor is not None:
            monitor.cancel()
        self._folder_menu_context.pop(menu_id, None)
        self._folder_menu_signal_connected.discard(menu_id)

    def _update_folder_pref(self, item: DockItem, key: str, value: Any) -> None:
        self._folder_stack.update_folder_pref(item, key, value)

    def _on_folder_sort_changed(
        self, widget: Gtk.MenuItem, item: DockItem, value: str
    ) -> None:
        if widget.get_active():
            self._update_folder_pref(item, "sort", value)

    def _on_folder_hidden_toggled(
        self, widget: Gtk.CheckMenuItem, item: DockItem
    ) -> None:
        self._update_folder_pref(item, "show_hidden", widget.get_active())
