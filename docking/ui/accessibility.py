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

"""Native GTK accessibility for the Cairo dock and a keyboard entry point.

Transparent, pointer-pass-through GTK buttons mirror the model and geometry.
GTK supplies the AT-SPI tree, lifetime, bounds, names and state notifications;
we do not maintain a second D-Bus accessibility implementation. A normal GTK
navigation window provides keyboard focus even on non-focusable dock surfaces.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Atk", "1.0")
from gi.repository import Atk, Gdk, GLib, Gtk

from docking.applets.base import Applet
from docking.core.items import APPLET_KIND, FOLDER_KIND, DockItem
from docking.i18n import _

if TYPE_CHECKING:
    import cairo

    from docking.ui.dock_window import DockWindow
    from docking.ui.input_controller import DockInputController
    from docking.ui.menu import MenuHandler


def item_description(item: DockItem) -> str:
    """Describe meaningful state without announcing animation frames."""
    parts = []
    if item.is_pinned:
        parts.append(_("Pinned"))
    if item.is_running:
        parts.append(_("Running"))
    if item.is_active:
        parts.append(_("Active"))
    if item.is_urgent:
        parts.append(_("Needs attention"))
    if item.instance_count:
        parts.append(_("Windows: {count}").format(count=item.instance_count))
    if item.badge_visible:
        parts.append(_("Notifications: {count}").format(count=item.badge_count))
    if item.progress_visible:
        parts.append(
            _("Progress: {percent}%").format(percent=round(item.progress * 100))
        )
    return ". ".join(parts)


class DockItemAccessible(Gtk.ButtonAccessible):
    """Expose activation, a context menu, and a usable focus destination."""

    __gtype_name__ = "DockingItemAccessible"

    def do_ref_state_set(self) -> Atk.StateSet:
        states = Gtk.ButtonAccessible.do_ref_state_set(self)
        if states is None:
            states = Atk.StateSet()
        if self.get_widget() is None:
            states.add_state(Atk.StateType.DEFUNCT)
        return states

    def do_atk_action_get_n_actions(self) -> int:
        widget = self.get_widget()
        if isinstance(widget, DockItemButton) and widget.manager.can_scroll(
            widget.item
        ):
            return 4
        return 2

    def do_atk_action_get_name(self, index: int) -> str | None:
        if 0 <= index < self.do_atk_action_get_n_actions():
            return ("activate", "menu", "scroll-up", "scroll-down")[index]
        return None

    def do_atk_action_get_localized_name(self, index: int) -> str | None:
        if 0 <= index < self.do_atk_action_get_n_actions():
            return (_("Activate"), _("Context menu"), _("Scroll up"), _("Scroll down"))[
                index
            ]
        return None

    def do_atk_action_get_description(self, index: int) -> str | None:
        return self.do_atk_action_get_localized_name(index)

    def do_atk_action_get_keybinding(self, index: int) -> str | None:
        if 0 <= index < self.do_atk_action_get_n_actions():
            return ("Return", "<Shift>F10", "Page_Up", "Page_Down")[index]
        return None

    def do_atk_action_do_action(self, index: int) -> bool:
        widget = self.get_widget()
        if (
            not isinstance(widget, DockItemButton)
            or not 0 <= index < self.do_atk_action_get_n_actions()
        ):
            return False
        if index >= 2:
            return widget.manager.request_scroll(widget.item, direction_up=index == 2)
        return widget.manager.request_action(widget.item, menu=index == 1)

    def do_atk_component_grab_focus(self) -> bool:
        widget = self.get_widget()
        if not isinstance(widget, DockItemButton):
            return False
        if widget.paint:
            widget.grab_focus()
            return True
        return widget.manager.focus(widget.item)


class DockItemButton(Gtk.Button):
    """An item proxy: native semantics, with painting owned by Cairo."""

    __gtype_name__ = "DockingItemButton"

    def __init__(
        self, manager: DockAccessibilityManager, item: DockItem, *, paint: bool = False
    ) -> None:
        super().__init__()
        self.manager = manager
        self.item = item
        self.paint = paint
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.set_can_focus(True)
        self.set_focus_on_click(False)
        self.get_style_context().add_class("dock-accessible-item")
        self.connect("clicked", lambda _button: manager.request_action(item))

    def do_draw(self, cr: cairo.Context) -> bool:
        if self.paint:
            return Gtk.Button.do_draw(self, cr)
        return True


# PyGObject exposes this GtkWidgetClass method on the Python class.
DockItemButton.set_accessible_type(DockItemAccessible)  # ty: ignore[missing-argument, invalid-argument-type]


class DockAccessibilityManager:
    """Keep accessible controls aligned with live item identities."""

    def __init__(
        self,
        window: DockWindow,
        input_controller: DockInputController,
        menu: MenuHandler,
    ) -> None:
        self.window = window
        self.input = input_controller
        self.menu = menu
        self.buttons: dict[int, DockItemButton] = {}
        self.navigator: Gtk.Window | None = None
        self.navigation_box: Gtk.Box | None = None
        self.options_button: Gtk.Button | None = None
        self.navigation_buttons: dict[int, Gtk.Button] = {}
        self.focused_item: DockItem | None = None
        self._handlers: list[tuple[Gtk.Widget, int]] = []
        self._pending: set[int] = set()
        self._started = False
        self._order: list[int] = []
        self.layer = Gtk.Fixed()
        self.layer.set_hexpand(True)
        self.layer.set_vexpand(True)
        self.layer.get_accessible().set_role(Atk.Role.TOOL_BAR)
        self.layer.get_accessible().set_name(_("Docking"))
        self.layer.get_accessible().set_description(_("Applications and applets"))
        window.accessibility_overlay.add_overlay(self.layer)
        window.accessibility_overlay.set_overlay_pass_through(self.layer, True)
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b".dock-accessible-item { padding: 0; margin: 0; border: 0; "
            b"min-width: 0; min-height: 0; }"
        )
        self._style = provider

    def start(self) -> None:
        """Subscribe after UI composition, before the first input draw."""
        if self._started:
            return
        self._started = True
        self.window.model.add_change_listener(self.sync)
        self.window.model.add_applet_change_listener(self._applet_changed)
        for widget, signal, callback in (
            (self.window.drawing_area, "draw", self._on_draw),
            (self.window, "destroy", self._on_destroy),
        ):
            self._handlers.append((widget, widget.connect(signal, callback)))
        self.sync()
        self.layer.show()

    def stop(self) -> None:
        """Release callbacks, queued actions, navigation, and proxy widgets."""
        if not self._started:
            return
        self._started = False
        self.close_navigation()
        for source in self._pending:
            GLib.source_remove(source)
        self._pending.clear()
        self.window.model.remove_change_listener(self.sync)
        self.window.model.remove_applet_change_listener(self._applet_changed)
        for widget, handler in self._handlers:
            widget.disconnect(handler)
        self._handlers.clear()
        for button in self.buttons.values():
            button.destroy()
        self.buttons.clear()
        self._order.clear()

    def _on_destroy(self, _window: Gtk.Window) -> None:
        self.stop()

    def items(self) -> list[DockItem]:
        """Exclude removed animation ghosts and decorative separators."""
        return [
            item
            for item in self.window.model.visible_items()
            if self.window.model.find_by_desktop_id(item.desktop_id) is item
            and item.desktop_id.split("#", 1)[0] != "applet://separator"
        ]

    def _live(self, item: DockItem) -> bool:
        return self._started and any(candidate is item for candidate in self.items())

    def _applet_changed(self, _desktop_id: str) -> None:
        self.sync()

    def sync(self) -> None:
        """Preserve objects across updates; remove stale actions immediately."""
        items = self.items()
        order = [id(item) for item in items]
        old_index = (
            self._order.index(id(self.focused_item))
            if id(self.focused_item) in self._order
            else 0
        )
        for key in list(self.buttons):
            if key not in order:
                self.buttons.pop(key).destroy()
        for item in items:
            key = id(item)
            if key not in self.buttons:
                button = DockItemButton(self, item)
                button.get_style_context().add_provider(
                    self._style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                )
                self.buttons[key] = button
                self.layer.put(button, 0, 0)
                button.show()
            accessible = self.buttons[key].get_accessible()
            name = item.name or item.desktop_id
            description = item_description(item)
            if accessible.get_name() != name:
                accessible.set_name(name)
            if accessible.get_description() != description:
                accessible.set_description(description)
        if order != self._order:
            # GtkFixed's child order is its accessible order. Reparent the same
            # controls on reorders so AT-SPI receives native remove/add events.
            for key in order:
                button = self.buttons[key]
                self.layer.remove(button)
                self.layer.put(button, 0, 0)
            self._order = order
        if self.focused_item is not None and not self._live(self.focused_item):
            self.focused_item = items[min(old_index, len(items) - 1)] if items else None
        self._sync_navigation(items)
        self.window.queue_redraw()

    def _on_draw(self, _area: Gtk.DrawingArea, _cr: cairo.Context) -> bool:
        frame = self.window._current_or_build_geometry_frame()
        for button in self.buttons.values():
            geometry = frame.geometry_for_item(button.item)
            rect = geometry.hit_rect if geometry else None
            visible = rect is not None and self.window.autohide.hide_offset < 1.0
            button.set_visible(visible)
            if rect is not None:
                self.layer.move(button, rect.x, rect.y)
                size = (max(1, rect.w), max(1, rect.h))
                if button.get_size_request() != size:
                    button.set_size_request(*size)
        return False

    def can_scroll(self, item: DockItem) -> bool:
        """Offer adjustment actions only for applets implementing wheel input."""
        applet = self.window.model.get_applet(item.desktop_id)
        return applet is not None and type(applet).on_scroll is not Applet.on_scroll

    def request_scroll(self, item: DockItem, *, direction_up: bool) -> bool:
        """Reuse applet wheel actions without moving focus or the pointer."""
        if not self._live(item) or not self.can_scroll(item):
            return False

        def dispatch() -> bool:
            self._pending.discard(source)
            if self._live(item):
                applet = self.window.model.get_applet(item.desktop_id)
                if applet is not None:
                    applet.on_scroll(direction_up)
                    self.window.queue_redraw()
            return False

        source = GLib.idle_add(dispatch)
        self._pending.add(source)
        return True

    def request_action(self, item: DockItem, *, menu: bool = False) -> bool:
        """Defer AT-SPI actions out of the D-Bus dispatch and recheck identity."""
        if not self._live(item):
            return False
        event = Gtk.get_current_event()

        def dispatch() -> bool:
            self._pending.discard(source)
            if self._live(item):
                if menu:
                    self.focus(item)
                    anchor = self.navigation_buttons.get(id(item))
                    if anchor is not None:
                        self.menu.show_for_accessibility(item, anchor, event)
                else:
                    if item.kind in (FOLDER_KIND, APPLET_KIND):
                        self.focus(item)
                        anchor = self.navigation_buttons.get(id(item))
                        if (
                            anchor is not None
                            and self.menu.show_stack_for_accessibility(
                                item, anchor, event
                            )
                        ):
                            return False
                    self.close_navigation()
                    frame = self.window._current_or_build_geometry_frame()
                    self.input.activate_item(item, frame=frame)
            return False

        source = GLib.idle_add(dispatch)
        self._pending.add(source)
        return True

    def focus(self, item: DockItem | None = None) -> bool:
        """Open a native focusable window, including on Wayland dock surfaces."""
        if not self._started or (item is not None and not self._live(item)):
            return False
        items = self.items()
        self.focused_item = item or self.focused_item or (items[0] if items else None)
        if self.navigator is None:
            navigator = Gtk.Window(title=_("Dock navigation"))
            navigator.set_default_size(360, 420)
            navigator.set_position(Gtk.WindowPosition.CENTER)
            navigator.connect("delete-event", self._delete_navigation)
            navigator.connect("key-press-event", self._on_key_press)
            self.navigator = navigator
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            outer.set_border_width(12)
            navigator.add(outer)
            instructions = Gtk.Label(
                label=_(
                    "Arrow keys to navigate; Enter to activate; "
                    "Shift+F10 for options; Escape to close."
                )
            )
            instructions.set_line_wrap(True)
            outer.pack_start(instructions, False, False, 0)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            outer.pack_start(scroll, True, True, 0)
            self.navigation_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=4
            )
            scroll.add(self.navigation_box)
            settings = Gtk.Button.new_with_label(_("Dock options"))
            self.options_button = settings
            settings.connect(
                "clicked", lambda button: self.menu.show_for_accessibility(None, button)
            )
            outer.pack_start(settings, False, False, 0)
            self._sync_navigation(items)
            navigator.show_all()
        self.window.autohide.set_keyboard_active(True)
        self.navigator.present()
        button = self.navigation_buttons.get(id(self.focused_item))
        if button is not None:
            button.grab_focus()
        return True

    def _sync_navigation(self, items: list[DockItem]) -> None:
        box = self.navigation_box
        if box is None:
            return
        keys = {id(item) for item in items}
        for key in list(self.navigation_buttons):
            if key not in keys:
                self.navigation_buttons.pop(key).destroy()
        for index, item in enumerate(items):
            key = id(item)
            if key not in self.navigation_buttons:
                button = DockItemButton(self, item, paint=True)
                button.connect("focus-in-event", self._navigation_focused, item)
                box.pack_start(button, False, False, 0)
                self.navigation_buttons[key] = button
                button.show()
            button = self.navigation_buttons[key]
            label = item.name or item.desktop_id
            if button.get_label() != label:
                button.set_label(label)
            description = item_description(item)
            accessible = button.get_accessible()
            if accessible.get_description() != description:
                accessible.set_description(description)
            box.reorder_child(button, index)
        if self.navigator is not None and self.navigator.get_focus() is None:
            button = self.navigation_buttons.get(id(self.focused_item))
            if button is not None:
                button.grab_focus()

    def _navigation_focused(
        self, _button: Gtk.Button, _event: Gdk.EventFocus, item: DockItem
    ) -> bool:
        self.focused_item = item
        return False

    def _on_key_press(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.close_navigation()
            return True
        if event.keyval in (Gdk.KEY_Page_Up, Gdk.KEY_Page_Down):
            if (
                self.focused_item is not None
                and self.navigator is not None
                and self.navigator.get_focus() is not self.options_button
            ):
                return self.request_scroll(
                    self.focused_item, direction_up=event.keyval == Gdk.KEY_Page_Up
                )
            return False
        if event.keyval == Gdk.KEY_Menu or (
            event.keyval == Gdk.KEY_F10 and event.state & Gdk.ModifierType.SHIFT_MASK
        ):
            if (
                self.navigator is not None
                and self.navigator.get_focus() is self.options_button
                and self.options_button is not None
            ):
                self.menu.show_for_accessibility(None, self.options_button)
            elif self.focused_item is not None:
                self.request_action(self.focused_item, menu=True)
            return True
        items = self.items()
        if not items or event.keyval not in (
            Gdk.KEY_Up,
            Gdk.KEY_Down,
            Gdk.KEY_Left,
            Gdk.KEY_Right,
            Gdk.KEY_Home,
            Gdk.KEY_End,
        ):
            return False
        index = next(
            (i for i, item in enumerate(items) if item is self.focused_item), 0
        )
        if event.keyval == Gdk.KEY_Home:
            index = 0
        elif event.keyval == Gdk.KEY_End:
            index = len(items) - 1
        else:
            index = (
                index + (1 if event.keyval in (Gdk.KEY_Down, Gdk.KEY_Right) else -1)
            ) % len(items)
        self.focused_item = items[index]
        self.navigation_buttons[id(items[index])].grab_focus()
        return True

    def _delete_navigation(self, _window: Gtk.Window, _event: Gdk.Event) -> bool:
        self.close_navigation()
        return True

    def close_navigation(self) -> None:
        """Release keyboard reveal without disturbing menu or drag inhibits."""
        navigator, self.navigator = self.navigator, None
        self.navigation_box = None
        self.options_button = None
        self.navigation_buttons.clear()
        self.focused_item = None
        if navigator is not None:
            navigator.destroy()
            self.window.autohide.set_keyboard_active(False)
