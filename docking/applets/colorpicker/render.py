"""Cairo icon rendering for Color Picker applet."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label


def create_icon(
    size: int,
    r: float,
    g: float,
    b: float,
    hex_label: str | None = None,
) -> GdkPixbuf.Pixbuf | None:
    """Render color swatch circle with optional hex label."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    cx = size / 2
    cy = size * 0.45
    radius = size * 0.34

    # Colored circle
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.set_source_rgb(r, g, b)
    cr.fill()

    # Thin white border
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.set_source_rgba(1, 1, 1, 0.8)
    cr.set_line_width(max(1.0, size * 0.03))
    cr.stroke()

    # Hex label at bottom
    if hex_label:
        draw_icon_label(cr=cr, text=hex_label, size=size)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
