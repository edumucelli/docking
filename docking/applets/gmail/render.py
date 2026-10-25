"""An original envelope icon drawn with Cairo, using the standard unread badge."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.draw import rounded_rect
from docking.ui.overlays import draw_warning_badge


def render_icon(size: int, *, warning: bool = False) -> GdkPixbuf.Pixbuf | None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    rounded_rect(
        cr=cr,
        x=size * 0.07,
        y=size * 0.16,
        width=size * 0.86,
        height=size * 0.68,
        radius=size * 0.1,
    )
    cr.set_source_rgb(0.96, 0.96, 0.95)
    cr.fill_preserve()
    cr.set_source_rgba(0.25, 0.27, 0.3, 0.3)
    cr.set_line_width(max(1, size * 0.025))
    cr.stroke()
    cr.set_source_rgb(0.83, 0.22, 0.19)
    cr.set_line_width(size * 0.095)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.move_to(size * 0.15, size * 0.76)
    cr.line_to(size * 0.15, size * 0.27)
    cr.line_to(size * 0.5, size * 0.53)
    cr.line_to(size * 0.85, size * 0.27)
    cr.line_to(size * 0.85, size * 0.76)
    cr.stroke()
    if warning:
        draw_warning_badge(cr=cr, size=size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
