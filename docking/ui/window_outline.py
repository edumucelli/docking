# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Click-through outline drawn around a foreign window on the desktop.

The preview popup uses this to show which real window a thumbnail stands for.
The overlay is a borderless, transparent window sized to the target's geometry
with an empty input shape, so pointer events and focus pass straight through.
Placement goes through the surface service: an override-redirect popup moved to
absolute coordinates on X11, a layer-shell surface on compositors that have one.
Callers gate construction on ``PlatformCapabilities.supports_window_outline``.
"""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk

from docking.platform.backends.base import Rect, SurfaceService

OUTLINE_WIDTH_PX = 4
OUTLINE_RGBA = (0.39, 0.71, 1.0, 0.95)


class WindowOutline(Gtk.Window):
    """Transparent, input-less window that strokes a rectangle border."""

    def __init__(self, surface: SurfaceService | None = None) -> None:
        self._surface = surface
        layered = surface is not None and surface.overlay_uses_toplevel
        super().__init__(
            type=Gtk.WindowType.TOPLEVEL if layered else Gtk.WindowType.POPUP
        )
        self.set_decorated(False)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
        self.set_app_paintable(True)

        visual = self.get_screen().get_rgba_visual()
        self._has_rgba_visual = visual is not None
        if visual:
            self.set_visual(visual)

        # An empty input shape makes the whole surface click-through. Set on the
        # widget (not the GdkWindow) so GTK reapplies it on every realize/map.
        self.input_shape_combine_region(cairo.Region([]))
        self.connect("draw", self._on_draw)
        if surface is not None:
            surface.prepare_overlay_window(self)

    def show_around(self, rect: Rect) -> None:
        """Outline ``rect`` (root coordinates in backend units); empty rects hide."""
        if rect.width <= 0 or rect.height <= 0:
            self.hide()
            return
        # Without a compositor the RGBA visual is still offered, but the cleared
        # interior is painted opaque and would cover the target window. Checked
        # on every call: a compositor can start or stop while the dock runs.
        if not (self._has_rgba_visual and self.get_screen().is_composited()):
            self.hide()
            return
        if self._surface is not None:
            self._surface.place_overlay(self, rect)
        else:
            logical = rect.device_to_logical(self.get_scale_factor())
            self.move(logical.x, logical.y)
            self.resize(logical.width, logical.height)
        self.show()
        self.queue_draw()

    @staticmethod
    def _on_draw(widget: Gtk.Widget, cr: cairo.Context) -> bool:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        half = OUTLINE_WIDTH_PX / 2
        cr.set_source_rgba(*OUTLINE_RGBA)
        cr.set_line_width(OUTLINE_WIDTH_PX)
        cr.rectangle(
            half,
            half,
            widget.get_allocated_width() - OUTLINE_WIDTH_PX,
            widget.get_allocated_height() - OUTLINE_WIDTH_PX,
        )
        cr.stroke()
        return True
