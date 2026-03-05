"""GTK lifecycle for Color Picker applet."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

from docking.applets.base import Applet
from docking.applets.colorpicker.render import create_icon
from docking.applets.colorpicker.state import pick_pixel, rgb_to_hex
from docking.applets.identity import AppletId
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="colorpicker"), applet_id=str(AppletId.COLORPICKER))

# Default grey swatch
_DEFAULT_RGB = (0.5, 0.5, 0.5)


class ColorPickerApplet(Applet):
    """Eyedropper color picker — click to sample, copies hex to clipboard.

    Left-click enters pick mode (fullscreen transparent overlay).
    Clicking anywhere samples the pixel color, copies hex to clipboard,
    and updates the icon swatch.
    """

    id = AppletId.COLORPICKER
    name = _("Color Picker")
    icon_name = "color-select"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._r, self._g, self._b = _DEFAULT_RGB
        self._hex = ""
        self._show_hex = True
        self._overlay: Gtk.Window | None = None

        if config:
            prefs = config.applet_prefs.get(AppletId.COLORPICKER, {})
            self._show_hex = prefs.get("show_hex", True)
            # Restore last picked color
            self._r = prefs.get("r", _DEFAULT_RGB[0])
            self._g = prefs.get("g", _DEFAULT_RGB[1])
            self._b = prefs.get("b", _DEFAULT_RGB[2])
            self._hex = prefs.get("hex", "")

        super().__init__(icon_size=icon_size, config=config)

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
        """Enter pick mode — fullscreen transparent overlay captures click."""
        self._start_pick()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []

        if self._hex:
            copy = Gtk.MenuItem(label=_("Copy {hex}").format(hex=self._hex))
            copy.connect("activate", lambda _: self._copy_to_clipboard())
            items.append(copy)

        show = Gtk.CheckMenuItem(label=_("Show Hex"))
        show.set_active(self._show_hex)
        show.connect("toggled", self._on_toggle_hex)
        items.append(show)

        return items

    def _on_toggle_hex(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_hex = widget.get_active()
        self._save()
        self.refresh_presentation()

    def _start_pick(self) -> None:
        """Create fullscreen transparent overlay to capture a click."""
        if self._overlay:
            return

        overlay = Gtk.Window(type=Gtk.WindowType.POPUP)
        overlay.set_decorated(False)
        overlay.set_app_paintable(True)

        screen = overlay.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            overlay.set_visual(visual)

        overlay.set_default_size(screen.get_width(), screen.get_height())
        overlay.move(0, 0)

        # Transparent background
        overlay.connect("draw", self._on_overlay_draw)
        overlay.set_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        overlay.connect("button-press-event", self._on_overlay_click)
        overlay.connect("key-press-event", self._on_overlay_key)

        # Crosshair cursor
        display = Gdk.Display.get_default()
        crosshair = Gdk.Cursor.new_for_display(display, Gdk.CursorType.CROSSHAIR)

        overlay.show_all()
        overlay.get_window().set_cursor(crosshair)

        # Grab pointer so click goes to overlay
        seat = display.get_default_seat()
        seat.grab(
            overlay.get_window(),
            Gdk.SeatCapabilities.ALL_POINTING,
            True,
            crosshair,
            None,
            None,
            None,
        )

        self._overlay = overlay

    @staticmethod
    def _on_overlay_draw(widget: Gtk.Window, cr) -> bool:
        cr.set_source_rgba(0, 0, 0, 0.01)  # near-transparent
        cr.paint()
        return True

    def _on_overlay_click(self, _widget: Gtk.Window, event: Gdk.EventButton) -> bool:
        """Sample pixel at click position."""
        self._dismiss_overlay()

        pixel = pick_pixel(x=int(event.x_root), y=int(event.y_root))
        if pixel:
            r, g, b = pixel
            self._r = r / 255.0
            self._g = g / 255.0
            self._b = b / 255.0
            self._hex = rgb_to_hex(r=r, g=g, b=b)
            self._copy_to_clipboard()
            self._save()
            self.refresh_presentation()

        return True

    def _on_overlay_key(self, _widget: Gtk.Window, event: Gdk.EventKey) -> bool:
        """Escape cancels pick mode."""
        if event.keyval == Gdk.KEY_Escape:
            self._dismiss_overlay()
        return True

    def _dismiss_overlay(self) -> None:
        if self._overlay:
            display = Gdk.Display.get_default()
            seat = display.get_default_seat()
            seat.ungrab()
            self._overlay.destroy()
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
