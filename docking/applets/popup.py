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

"""Shared GTK surface helpers for applet-owned secondary UI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from docking.core.position import Position
from docking.ui.display import clamp_popup, clamp_to_screen, get_pointer_position

_POPUP_CLASS = "applet-popup-surface"
_POPUP_CSS = f"""
.{_POPUP_CLASS} {{
    background-color: @theme_bg_color;
    color: @theme_fg_color;
    border: 1px solid alpha(@theme_fg_color, 0.16);
    border-radius: 12px;
}}
""".encode()

_popup_css_provider: Gtk.CssProvider | None = None

DEFAULT_POPUP_CURSOR_GAP_PX = 20
DEFAULT_DIALOG_CONTENT_SPACING_PX = 8
DEFAULT_DIALOG_MARGIN_PX = 12


@dataclass(frozen=True, slots=True)
class PopupAnchor:
    """Screen-space applet popup anchor."""

    x: int
    y: int
    position: Position
    parent: Gtk.Window | None = None


def entry_completion_combo(
    *,
    matches: Callable[[str, str], bool] | None = None,
) -> Gtk.ComboBoxText:
    """Create an entry-backed text combo with inline/popup completion."""
    combo = Gtk.ComboBoxText.new_with_entry()
    combo.set_entry_text_column(0)

    completion = Gtk.EntryCompletion()
    completion.set_model(combo.get_model())
    completion.set_text_column(0)
    completion.set_inline_completion(True)
    completion.set_popup_completion(True)
    completion.set_match_func(_completion_matches, matches or _prefix_matches)
    entry = combo.get_child()
    entry.set_completion(completion)
    entry.connect("focus-in-event", _select_combo_entry_text)
    entry.connect("button-release-event", _select_combo_entry_text)
    return combo


def _prefix_matches(text: str, label: str) -> bool:
    """Return true when typed text starts the visible label."""
    needle = text.strip().casefold()
    return not needle or label.strip().casefold().startswith(needle)


def _completion_matches(
    completion: Gtk.EntryCompletion,
    key: str,
    tree_iter,
    matches: Callable[[str, str], bool],
) -> bool:
    model = completion.get_model()
    if model is None:
        return False
    return matches(key, str(model.get_value(tree_iter, 0)))


def _select_combo_entry_text(entry: Gtk.Entry, *_args) -> bool:
    entry.select_region(0, -1)
    return False


def ensure_popup_css() -> None:
    """Install the shared popup CSS once per screen."""
    global _popup_css_provider
    if _popup_css_provider is not None:
        return

    screen = Gdk.Screen.get_default()
    if screen is None:
        return

    provider = Gtk.CssProvider()
    provider.load_from_data(_POPUP_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _popup_css_provider = provider


def wrap_popup(content: Gtk.Widget) -> Gtk.Frame:
    """Wrap popup content in a GTK-themed surface instead of custom painting."""
    ensure_popup_css()

    frame = Gtk.Frame()
    frame.set_shadow_type(Gtk.ShadowType.NONE)
    frame.get_style_context().add_class(_POPUP_CLASS)
    frame.add(content)
    return frame


def create_popup_window() -> Gtk.Window:
    """Create a transient, undecorated applet popup window."""
    window = Gtk.Window(type=Gtk.WindowType.POPUP)
    window.set_decorated(False)
    window.set_skip_taskbar_hint(True)
    window.set_resizable(False)
    window.set_accept_focus(True)
    window.set_focus_on_map(True)
    window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
    return window


def show_wrapped_popup(
    *,
    window: Gtk.Window,
    content: Gtk.Widget,
    gap_px: int = DEFAULT_POPUP_CURSOR_GAP_PX,
    anchor: PopupAnchor | None = None,
) -> None:
    """Replace popup content, show it, and place it near the applet anchor."""
    child = window.get_child()
    if child:
        window.remove(child)

    if anchor is not None and anchor.parent is not None:
        window.set_transient_for(anchor.parent)
        window.set_attached_to(anchor.parent)
    window.add(wrap_popup(content))
    window.show_all()
    if anchor is not None:
        position_popup_near_anchor(window=window, anchor=anchor, gap_px=gap_px)
    else:
        position_popup_near_pointer(window=window, gap_px=gap_px)


def position_popup_near_anchor(
    *,
    window: Gtk.Window,
    anchor: PopupAnchor,
    gap_px: int = DEFAULT_POPUP_CURSOR_GAP_PX,
) -> None:
    """Position a popup near an icon anchor, clamped to the current screen."""
    pref = window.get_preferred_size()[1]
    popup_w = max(pref.width, 1)
    popup_h = max(pref.height, 1)

    if anchor.position == Position.BOTTOM:
        popup_x = int(anchor.x - popup_w / 2)
        popup_y = int(anchor.y - popup_h - gap_px)
    elif anchor.position == Position.TOP:
        popup_x = int(anchor.x - popup_w / 2)
        popup_y = int(anchor.y + gap_px)
    elif anchor.position == Position.LEFT:
        popup_x = int(anchor.x + gap_px)
        popup_y = int(anchor.y - popup_h / 2)
    else:
        popup_x = int(anchor.x - popup_w - gap_px)
        popup_y = int(anchor.y - popup_h / 2)

    popup_pos = clamp_popup(window, popup_x, popup_y, popup_w, popup_h)
    window.move(popup_pos.x, popup_pos.y)


def position_popup_near_pointer(
    *,
    window: Gtk.Window,
    gap_px: int = DEFAULT_POPUP_CURSOR_GAP_PX,
) -> None:
    """Position a popup above the pointer, clamped to the current screen."""
    display = Gdk.Display.get_default()
    pos = get_pointer_position(display) if display is not None else None
    mouse_x = pos.x if pos is not None else 0
    mouse_y = pos.y if pos is not None else 0

    pref = window.get_preferred_size()[1]
    popup_w = max(pref.width, 1)
    popup_h = max(pref.height, 1)

    screen = window.get_screen()
    popup_x = int(mouse_x - popup_w / 2)
    popup_y = int(mouse_y - popup_h - gap_px)
    popup_pos = clamp_to_screen(
        popup_x,
        popup_y,
        popup_w,
        popup_h,
        screen.get_width(),
        screen.get_height(),
    )
    window.move(popup_pos.x, popup_pos.y)


def prepare_dialog_content(
    *,
    dialog: Gtk.Dialog,
    width: int | None = None,
    height: int = -1,
    spacing: int = DEFAULT_DIALOG_CONTENT_SPACING_PX,
    margin: int = DEFAULT_DIALOG_MARGIN_PX,
    default_response: int | None = None,
    resizable: bool | None = None,
) -> Gtk.Box:
    """Apply standard applet dialog sizing, placement, and content spacing."""
    # Window-manager hints: applet-owned dialogs are secondary UI, so they
    # should not become separate dock/task-list/pager entries on X11.
    dialog.set_skip_taskbar_hint(True)
    dialog.set_skip_pager_hint(True)

    # Window geometry: make dialogs open near the pointer and honor optional
    # caller-provided size, resize, and default-response settings.
    if width is not None:
        dialog.set_default_size(width, height)
    dialog.set_position(Gtk.WindowPosition.MOUSE)
    if resizable is not None:
        dialog.set_resizable(resizable)
    if default_response is not None:
        dialog.set_default_response(default_response)

    # Content layout: standardize spacing and margins for applet dialog bodies.
    box = dialog.get_content_area()
    box.set_spacing(spacing)
    box.set_margin_start(margin)
    box.set_margin_end(margin)
    box.set_margin_top(margin)
    box.set_margin_bottom(margin)
    return box


def add_cancel_ok_buttons(
    *,
    dialog: Gtk.Dialog,
    ok_label: str | None = None,
    cancel_label: str | None = None,
) -> None:
    """Add cancel then OK buttons in the standard GTK response order."""
    dialog.add_buttons(
        cancel_label or Gtk.STOCK_CANCEL,
        Gtk.ResponseType.CANCEL,
        ok_label or Gtk.STOCK_OK,
        Gtk.ResponseType.OK,
    )


def create_capture_overlay(
    *,
    draw_handler: Callable[[Gtk.Window, object], bool],
    click_handler: Callable[[Gtk.Window, Gdk.EventButton], bool],
    key_handler: Callable[[Gtk.Window, Gdk.EventKey], bool],
    cursor_type: Gdk.CursorType,
) -> Gtk.Window:
    """Create a fullscreen pointer-capture overlay with Escape handled by caller."""
    overlay = Gtk.Window(type=Gtk.WindowType.POPUP)
    overlay.set_decorated(False)
    overlay.set_app_paintable(True)

    screen = overlay.get_screen()
    visual = screen.get_rgba_visual()
    if visual:
        overlay.set_visual(visual)

    overlay.set_default_size(screen.get_width(), screen.get_height())
    overlay.move(0, 0)
    overlay.connect("draw", draw_handler)
    overlay.set_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.KEY_PRESS_MASK)
    overlay.connect("button-press-event", click_handler)
    overlay.connect("key-press-event", key_handler)

    display = Gdk.Display.get_default()
    cursor = Gdk.Cursor.new_for_display(display, cursor_type)
    overlay.show_all()
    overlay.get_window().set_cursor(cursor)

    seat = display.get_default_seat()
    seat.grab(
        overlay.get_window(),
        Gdk.SeatCapabilities.ALL_POINTING | Gdk.SeatCapabilities.KEYBOARD,
        True,
        cursor,
        None,
        None,
        None,
    )
    return overlay


def dismiss_capture_overlay(overlay: Gtk.Window | None) -> None:
    """Release the active grab and destroy a capture overlay."""
    if overlay is None:
        return
    display = Gdk.Display.get_default()
    seat = display.get_default_seat()
    seat.ungrab()
    overlay.destroy()


def draw_transparent_capture_overlay(_widget: Gtk.Window, cr) -> bool:
    """Paint a near-transparent surface so the overlay receives events."""
    cr.set_source_rgba(0, 0, 0, 0.01)
    cr.paint()
    return True
