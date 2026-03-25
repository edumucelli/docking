"""Procedural icon rendering for the Window Killer applet.

The icon is intentionally blunt. A red crosshair reads as destructive and
selection-oriented even at small sizes, which matches the applet's job better
than a literal skull or generic close-button glyph would.

As with the other small utility applets, Cairo rendering keeps the icon simple,
resolution-independent, and easy to tweak alongside the rest of the codebase.
"""

from __future__ import annotations

import math

import cairo
from gi.repository import Gdk, GdkPixbuf


def create_icon(size: int) -> GdkPixbuf.Pixbuf | None:
    """Render a crosshair/skull icon representing window killing."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    cx = size / 2
    cy = size * 0.45
    r = size * 0.3
    lw = max(1.5, size * 0.05)

    cr.set_source_rgba(0.9, 0.25, 0.25, 0.9)
    cr.set_line_width(lw)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    # Circle
    cr.arc(cx, cy, r, 0, math.tau)
    cr.stroke()

    # X inside
    off = r * 0.55
    cr.move_to(cx - off, cy - off)
    cr.line_to(cx + off, cy + off)
    cr.stroke()
    cr.move_to(cx + off, cy - off)
    cr.line_to(cx - off, cy + off)
    cr.stroke()

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
