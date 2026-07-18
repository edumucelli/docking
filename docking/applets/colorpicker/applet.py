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

"""GTK lifecycle for Color Picker applet."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk

from docking.applets.base import Applet
from docking.applets.colorpicker import meta
from docking.applets.colorpicker.render import create_icon
from docking.applets.colorpicker.state import rgb_to_hex
from docking.applets.menu import menu_sections
from docking.applets.popup import (
    create_capture_overlay,
    dismiss_capture_overlay,
    draw_transparent_capture_overlay,
)
from docking.applets.services import AppletServices
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.backends.base import ScreenCaptureService

log = with_context(get_logger(name="colorpicker"), applet_id=meta.id)

# Default grey swatch
_DEFAULT_RGB = (0.5, 0.5, 0.5)


class ColorPickerApplet(Applet):
    """Eyedropper color picker - click to sample, copies hex to clipboard.

    Left-click enters pick mode (fullscreen transparent overlay).
    Clicking anywhere samples the pixel color, copies hex to clipboard,
    and updates the icon swatch.
    """

    id = meta.id
    name = _("Color Picker")
    icon_name = "color-select"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._r, self._g, self._b = _DEFAULT_RGB
        self._hex = ""
        self._show_hex = True
        self._overlay: Gtk.Window | None = None
        self._screen_capture: ScreenCaptureService | None = None

        if config:
            prefs = config.applet_prefs.get(meta.id, {})
            self._show_hex = prefs.get("show_hex", True)
            # Restore last picked color
            self._r = prefs.get("r", _DEFAULT_RGB[0])
            self._g = prefs.get("g", _DEFAULT_RGB[1])
            self._b = prefs.get("b", _DEFAULT_RGB[2])
            self._hex = prefs.get("hex", "")

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def set_services(self, services: AppletServices) -> None:
        self._screen_capture = services.screen_capture

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(
            size=size,
            r=self._r,
            g=self._g,
            b=self._b,
            hex_label=self._hex if self._show_hex and self._hex else None,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = self._hex if self._hex else _("Color Picker")

    def on_clicked(self) -> None:
        """Enter pick mode - fullscreen transparent overlay captures click."""
        self._start_pick()

    def stop(self) -> None:
        self._dismiss_overlay()
        super().stop()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        primary: list[Gtk.MenuItem] = []

        if self._hex:
            copy = Gtk.MenuItem(label=_("Copy {hex}").format(hex=self._hex))
            copy.connect("activate", lambda _: self._copy_to_clipboard())
            primary.append(copy)

        show = Gtk.CheckMenuItem(label=_("Show Hex"))
        show.set_active(self._show_hex)
        show.connect("toggled", self._on_toggle_hex)

        return menu_sections(primary=primary, display=[show], gtk=Gtk)

    def _on_toggle_hex(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_hex = widget.get_active()
        self._save()
        self.present()

    def _start_pick(self) -> None:
        """Create fullscreen transparent overlay to capture a click."""
        if self._overlay or self._screen_capture is None:
            return

        self._overlay = create_capture_overlay(
            draw_handler=self._on_overlay_draw,
            click_handler=self._on_overlay_click,
            key_handler=self._on_overlay_key,
            cursor_type=Gdk.CursorType.CROSSHAIR,
        )
        if self._overlay is not None:
            self.register_popup_surface(self._overlay)

    @staticmethod
    def _on_overlay_draw(widget: Gtk.Window, cr) -> bool:
        return draw_transparent_capture_overlay(widget, cr)

    def _on_overlay_click(self, _widget: Gtk.Window, event: Gdk.EventButton) -> bool:
        """Sample pixel at click position."""
        self._dismiss_overlay()

        pixel = (
            self._screen_capture.pick_color(x=int(event.x_root), y=int(event.y_root))
            if self._screen_capture is not None
            else None
        )
        if pixel:
            r, g, b = pixel
            self._r = r / 255.0
            self._g = g / 255.0
            self._b = b / 255.0
            self._hex = rgb_to_hex(r=r, g=g, b=b)
            self._copy_to_clipboard()
            self._save()
            self.present()

        return True

    def _on_overlay_key(self, _widget: Gtk.Window, event: Gdk.EventKey) -> bool:
        """Escape cancels pick mode."""
        if event.keyval == Gdk.KEY_Escape:
            self._dismiss_overlay()
        return True

    def _dismiss_overlay(self) -> None:
        if self._overlay:
            dismiss_capture_overlay(self._overlay)
            self._overlay = None

    def _copy_to_clipboard(self) -> None:
        if not self._hex:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(self._hex, -1)
        clipboard.store()

    def _save(self) -> None:
        self.save_prefs(
            prefs={
                "show_hex": self._show_hex,
                "r": self._r,
                "g": self._g,
                "b": self._b,
                "hex": self._hex,
            }
        )
