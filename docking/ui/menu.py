"""Right-click context menus for dock items and the dock itself."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402

from docking.core.position import Position
from docking.core.theme import Theme, _BUILTIN_THEMES_DIR
from docking.core.zoom import compute_layout

if TYPE_CHECKING:
    from docking.core.zoom import LayoutItem
    from docking.ui.dock_window import DockWindow
    from docking.platform.model import DockModel, DockItem
    from docking.core.config import Config
    from docking.platform.window_tracker import WindowTracker


ICON_SIZE_OPTIONS = (32, 48, 64, 80)


class MenuHandler:
    """Builds and shows context menus for dock items."""

    def __init__(
        self,
        window: DockWindow,
        model: DockModel,
        config: Config,
        tracker: WindowTracker,
    ) -> None:
        self._window = window
        self._model = model
        self._config = config
        self._tracker = tracker

    def show(self, event: Gdk.EventButton, cursor_main: float) -> None:
        """Show context menu at cursor position."""
        items = self._model.visible_items()
        theme = self._window.theme
        local_main = self._window.local_cursor_main()
        layout = compute_layout(
            items,
            self._config,
            local_main,
            item_padding=theme.item_padding,
            h_padding=theme.h_padding,
        )
        item = self._hit_test(cursor_main, items, layout)

        menu = Gtk.Menu()

        if item:
            self._build_item_menu(menu, item)
        else:
            self._build_dock_menu(menu)

        menu.show_all()
        menu.popup_at_pointer(event)

    def _build_item_menu(self, menu: Gtk.Menu, item: DockItem) -> None:
        """Build context menu for a specific dock item."""
        # App name as header (insensitive)
        header = Gtk.MenuItem(label=item.name)
        header.set_sensitive(False)
        menu.append(header)
        menu.append(Gtk.SeparatorMenuItem())

        # Pin/Unpin
        if item.is_pinned:
            unpin = Gtk.MenuItem(label="Remove from Dock")
            unpin.connect("activate", lambda _: self._model.unpin_item(item.desktop_id))
            menu.append(unpin)
        else:
            pin = Gtk.MenuItem(label="Keep in Dock")
            pin.connect("activate", lambda _: self._model.pin_item(item.desktop_id))
            menu.append(pin)

        # Close all (if running)
        if item.is_running and item.instance_count > 0:
            menu.append(Gtk.SeparatorMenuItem())
            label = "Close All" if item.instance_count > 1 else "Close"
            close = Gtk.MenuItem(label=label)
            close.connect(
                "activate",
                lambda _: self._tracker.close_all(item.desktop_id),
            )
            menu.append(close)

    def _build_dock_menu(self, menu: Gtk.Menu) -> None:
        """Build context menu for the dock background."""
        # Auto-hide toggle
        autohide = Gtk.CheckMenuItem(label="Auto-hide")
        autohide.set_active(self._config.autohide)
        autohide.connect("toggled", self._on_autohide_toggled)
        menu.append(autohide)

        # Window previews toggle
        previews = Gtk.CheckMenuItem(label="Window Previews")
        previews.set_active(self._config.previews_enabled)
        previews.connect("toggled", self._on_previews_toggled)
        menu.append(previews)

        menu.append(Gtk.SeparatorMenuItem())

        # Themes submenu
        theme_item = Gtk.MenuItem(label="Themes")
        theme_menu = Gtk.Menu()
        first_radio: Gtk.RadioMenuItem | None = None

        theme_names = [p.stem for p in sorted(_BUILTIN_THEMES_DIR.glob("*.json"))]

        for name in theme_names:
            label = name.replace("-", " ").capitalize()
            radio = Gtk.RadioMenuItem(label=label)
            if first_radio:
                radio.join_group(first_radio)
            else:
                first_radio = radio
            if name == self._config.theme:
                radio.set_active(True)
            radio.connect("activate", self._on_theme_changed, name)
            theme_menu.append(radio)
        theme_item.set_submenu(theme_menu)
        menu.append(theme_item)

        menu.append(Gtk.SeparatorMenuItem())

        # Icon size submenu
        size_item = Gtk.MenuItem(label="Icon Size")
        size_menu = Gtk.Menu()
        for size in ICON_SIZE_OPTIONS:
            item = Gtk.RadioMenuItem(label=f"{size}px")
            if size == self._config.icon_size:
                item.set_active(True)
            item.connect("activate", self._on_icon_size_changed, size)
            size_menu.append(item)
        size_item.set_submenu(size_menu)
        menu.append(size_item)

        # Position submenu
        pos_item = Gtk.MenuItem(label="Position")
        pos_menu = Gtk.Menu()
        first_pos_radio: Gtk.RadioMenuItem | None = None
        for pos in Position:
            radio = Gtk.RadioMenuItem(label=pos.value.capitalize())
            if first_pos_radio:
                radio.join_group(first_pos_radio)
            else:
                first_pos_radio = radio
            if pos.value == self._config.position:
                radio.set_active(True)
            radio.connect("activate", self._on_position_changed, pos.value)
            pos_menu.append(radio)
        pos_item.set_submenu(pos_menu)
        menu.append(pos_item)

        menu.append(Gtk.SeparatorMenuItem())

        # Quit
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        menu.append(quit_item)

    def _on_autohide_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._config.autohide = widget.get_active()
        self._config.save()
        # Reset hide state when toggling off so dock becomes visible immediately
        if not self._config.autohide and self._window.autohide:
            self._window.autohide.reset()
        # Update struts immediately so windows adapt to the new mode:
        # autohide on  -> clear struts (windows use full screen)
        # autohide off -> set struts (windows shrink above dock)
        self._window.update_struts()

    def _on_previews_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._config.previews_enabled = widget.get_active()
        self._config.save()

    def _on_theme_changed(self, widget: Gtk.MenuItem, name: str) -> None:
        if not widget.get_active() or name == self._config.theme:
            return
        self._config.theme = name
        self._config.save()
        new_theme = Theme.load(name, self._config.icon_size)
        self._window.theme = new_theme
        self._window.update_struts()
        self._window.drawing_area.queue_draw()

    def _on_position_changed(self, widget: Gtk.MenuItem, position: str) -> None:
        if not widget.get_active() or position == self._config.position:
            return
        self._config.position = position
        self._config.save()
        self._window.reposition()

    def _on_icon_size_changed(self, widget: Gtk.MenuItem, size: int) -> None:
        if widget.get_active():
            self._config.icon_size = size
            self._config.save()
            # Would need a full reload to update icons at new size

    def _hit_test(
        self,
        main_coord: float,
        items: list[DockItem],
        layout: list[LayoutItem],
    ) -> DockItem | None:
        """Find which DockItem is under cursor along the main axis."""
        offset = self._window.zoomed_main_offset(layout)
        for i, li in enumerate(layout):
            icon_width = li.scale * self._config.icon_size
            left = li.x + offset
            if left <= main_coord <= left + icon_width:
                return items[i]
        return None
