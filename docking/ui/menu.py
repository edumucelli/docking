"""Right-click context menus for dock items and the dock itself."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from docking.applets.base import is_applet
from docking.core.position import Position
from docking.core.theme import _BUILTIN_THEMES_DIR, Theme
from docking.core.zoom import compute_layout

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.zoom import LayoutItem
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockItem, DockModel
    from docking.platform.window_tracker import WindowTracker
    from docking.ui.dock_window import DockWindow


ICON_SIZE_OPTIONS = (32, 48, 64, 80)


def _build_radio_submenu(
    label: str,
    items: Sequence[tuple[str, Any]],
    current: Any,
    on_changed: Any,
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


class MenuHandler:
    """Builds and shows context menus for dock items."""

    def __init__(
        self,
        window: DockWindow,
        model: DockModel,
        config: Config,
        tracker: WindowTracker,
        launcher: Launcher | None = None,
    ) -> None:
        self._window = window
        self._model = model
        self._config = config
        self._tracker = tracker
        self._launcher = launcher

    def show(self, event: Gdk.EventButton, cursor_main: float) -> None:
        """Build and show the right-click context menu.

        Hit-tests the cursor to determine whether to show an item-specific
        menu (desktop actions, pin/unpin, close) or a dock background menu
        (autohide, theme, position, applets, quit).
        """
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
        """Build context menu for a specific dock item.

        Applets: delegates to applet.get_menu_items() + "Remove from Dock".
        Regular items: desktop actions (quicklists), pin/unpin, close.
        """
        if is_applet(item.desktop_id):
            # Applet-specific menu items
            applet = self._model.get_applet(item.desktop_id)
            if applet:
                for mi in applet.get_menu_items():
                    menu.append(mi)
                if applet.get_menu_items():
                    menu.append(Gtk.SeparatorMenuItem())
            remove = Gtk.MenuItem(label="Remove from Dock")
            remove.connect(
                "activate",
                lambda _: self._model.remove_applet(item.desktop_id),
            )
            menu.append(remove)
            return

        # Desktop actions (e.g. "New Window", "New Incognito Window")
        self._append_desktop_actions(menu, item.desktop_id)

        # Pin/Unpin
        if item.is_pinned:
            unpin = Gtk.MenuItem(label="Remove from Dock")
            unpin.connect("activate", lambda _: self._model.unpin_item(item.desktop_id))
            menu.append(unpin)
        else:
            pin = Gtk.MenuItem(label="Keep in Dock")
            pin.connect("activate", lambda _: self._model.pin_item(item.desktop_id))
            menu.append(pin)

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
        """Build context menu for the dock background (no item under cursor).

        Sections: autohide toggle, preview toggle, theme/icon size/position
        radio submenus, applet toggle checkboxes, quit.
        """
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
        theme_names = [p.stem for p in sorted(_BUILTIN_THEMES_DIR.glob("*.json"))]
        theme_items = [(n.replace("-", " ").capitalize(), n) for n in theme_names]
        menu.append(
            _build_radio_submenu(
                "Themes", theme_items, self._config.theme, self._on_theme_changed
            )
        )

        menu.append(Gtk.SeparatorMenuItem())

        # Icon size submenu
        size_items = [(f"{s}px", s) for s in ICON_SIZE_OPTIONS]
        menu.append(
            _build_radio_submenu(
                "Icon Size",
                size_items,
                self._config.icon_size,
                self._on_icon_size_changed,
            )
        )

        # Position submenu
        pos_items = [(p.value.capitalize(), p.value) for p in Position]
        menu.append(
            _build_radio_submenu(
                "Position", pos_items, self._config.position, self._on_position_changed
            )
        )

        # Applets submenu -- toggle each applet on/off
        from docking.applets import get_registry

        registry = get_registry()
        if registry:
            dock_item = Gtk.MenuItem(label="Applets")
            dock_menu = Gtk.Menu()
            active_ids = {
                item.desktop_id
                for item in self._model.pinned_items
                if is_applet(item.desktop_id)
            }
            for did, cls in sorted(registry.items()):
                desktop_id = f"applet://{did}"
                check = Gtk.CheckMenuItem(label=cls.name)
                check.set_active(desktop_id in active_ids)
                check.connect("toggled", self._on_applet_toggled, did)
                dock_menu.append(check)
            dock_item.set_submenu(dock_menu)
            menu.append(dock_item)

        menu.append(Gtk.SeparatorMenuItem())

        # Quit
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _: Gtk.main_quit())
        menu.append(quit_item)

    def _append_desktop_actions(self, menu: Gtk.Menu, desktop_id: str) -> None:
        """Append desktop actions (quicklists) from .desktop file, if any."""
        if not self._launcher:
            return
        from docking.platform.launcher import get_actions, launch_action

        actions = get_actions(desktop_id)
        if not actions:
            return
        for action_id, label in actions:
            mi = Gtk.MenuItem(label=label)
            # Capture by value via default arg
            mi.connect(
                "activate",
                lambda _, did=desktop_id, aid=action_id: launch_action(did, aid),
            )
            menu.append(mi)
        menu.append(Gtk.SeparatorMenuItem())

    def _on_applet_toggled(self, widget: Gtk.CheckMenuItem, applet_id: str) -> None:
        if widget.get_active():
            self._model.add_applet(applet_id)
        else:
            self._model.remove_applet(f"applet://{applet_id}")

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
