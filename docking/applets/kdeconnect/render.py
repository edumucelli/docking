"""Original phone artwork with an exact battery percentage, including zero."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label
from docking.applets.draw import rounded_rect


def render_icon(
    *,
    size: int,
    connected: bool = False,
    charge: int | None = None,
    charging: bool = False,
) -> GdkPixbuf.Pixbuf | None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    rounded_rect(
        cr=cr,
        x=size * 0.23,
        y=size * 0.05,
        width=size * 0.54,
        height=size * 0.85,
        radius=size * 0.09,
    )
    cr.set_source_rgb(0.18, 0.24, 0.31)
    cr.fill_preserve()
    cr.set_source_rgb(0.7, 0.77, 0.84)
    cr.set_line_width(max(1, size * 0.025))
    cr.stroke()
    rounded_rect(
        cr=cr,
        x=size * 0.28,
        y=size * 0.14,
        width=size * 0.44,
        height=size * 0.6,
        radius=size * 0.02,
    )
    cr.set_source_rgb(*((0.15, 0.67, 0.84) if connected else (0.37, 0.42, 0.46)))
    cr.fill()
    if connected and charge is not None:
        cr.rectangle(
            size * 0.32,
            size * 0.63 - size * 0.43 * charge / 100,
            size * 0.36,
            size * 0.43 * charge / 100,
        )
        cr.set_source_rgba(0.27, 0.91, 0.60, 0.75)
        cr.fill()
    cr.arc(size * 0.5, size * 0.81, size * 0.025, 0, 6.2832)
    cr.set_source_rgb(0.85, 0.91, 0.96)
    cr.fill()
    if charging:
        cr.move_to(size * 0.52, size * 0.26)
        cr.line_to(size * 0.4, size * 0.45)
        cr.line_to(size * 0.5, size * 0.45)
        cr.line_to(size * 0.46, size * 0.6)
        cr.line_to(size * 0.61, size * 0.4)
        cr.line_to(size * 0.51, size * 0.4)
        cr.close_path()
        cr.set_source_rgb(1, 1, 0.7)
        cr.fill()
    if connected and charge is not None:
        draw_icon_label(cr=cr, text=f"{charge}%", size=size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
