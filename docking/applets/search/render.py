"""Cairo rendering for the Search applet."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf


def render_icon(size: int) -> GdkPixbuf.Pixbuf | None:
    """Render a clean magnifying-glass launcher icon."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    center_x = size * 0.43
    center_y = size * 0.41
    radius = size * 0.23
    line_width = max(2.0, size * 0.095)

    cr.set_line_width(line_width)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_source_rgba(0.22, 0.58, 0.94, 1.0)
    cr.arc(center_x, center_y, radius, 0, 2 * math.pi)
    cr.stroke()

    handle_start = radius / math.sqrt(2)
    cr.move_to(center_x + handle_start, center_y + handle_start)
    cr.line_to(size * 0.78, size * 0.78)
    cr.stroke()

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


__all__ = ["render_icon"]
