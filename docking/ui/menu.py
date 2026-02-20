"""Right-click context menus for dock items and the dock itself."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402

if TYPE_CHECKING:
    from docking.ui.dock_window import DockWindow
    from docking.platform.model import DockModel, DockItem
    from docking.core.config import Config
    from docking.platform.window_tracker import WindowTracker


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

    def show(self, event: Gdk.EventButton, cursor_x: float) -> None:
        """Show context menu at cursor position."""
        from docking.core.zoom import compute_layout
        items = self._model.visible_items()
        theme = self._window.theme
        local_x = self._window._local_cursor_x()
        layout = compute_layout(
            items, self._config, local_x,
            item_padding=theme.item_padding,
            h_padding=theme.h_padding,
        )
        item = self._hit_test(cursor_x, items, layout)  # cursor_x is window-space for hit test

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

        menu.append(Gtk.SeparatorMenuItem())

        # Icon size submenu
        size_item = Gtk.MenuItem(label="Icon Size")
        size_menu = Gtk.Menu()
        for size in (32, 48, 64, 80):
            item = Gtk.RadioMenuItem(label=f"{size}px")
            if size == self._config.icon_size:
                item.set_active(True)
            item.connect("activate", self._on_icon_size_changed, size)
            size_menu.append(item)
        size_item.set_submenu(size_menu)
        menu.append(size_item)

        menu.append(Gtk.SeparatorMenuItem())

        # Quit
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        menu.append(quit_item)

    def _on_autohide_toggled(self, widget: Gtk.CheckMenuItem) -> None:
        self._config.autohide = widget.get_active()
        self._config.save()
        # Reset hide state when toggling off so dock becomes visible immediately
        if not self._config.autohide and self._window._autohide:
            self._window._autohide.reset()

    def _on_icon_size_changed(self, widget: Gtk.MenuItem, size: int) -> None:
        if widget.get_active():
            self._config.icon_size = size
            self._config.save()
            # Would need a full reload to update icons at new size

    def _hit_test(
        self, x: float, items: list, layout: list,
    ) -> DockItem | None:
        """Find which DockItem is under cursor x (window-space)."""
        offset = self._window._zoomed_x_offset(layout)
        for i, li in enumerate(layout):
            icon_w = li.scale * self._config.icon_size
            left = li.x + offset
            if left <= x <= left + icon_w:
                return items[i]
        return None
