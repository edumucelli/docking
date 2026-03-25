"""Icon rendering for Unit Converter applet."""

from __future__ import annotations

import cairo
from gi.repository import Gdk, GdkPixbuf


def create_icon(size: int) -> GdkPixbuf.Pixbuf | None:
    """Render a bidirectional arrow icon representing unit conversion."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    cx = size / 2
    cy = size / 2
    arrow_len = size * 0.3
    head = size * 0.1
    gap = size * 0.14

    cr.set_line_width(max(1.5, size * 0.04))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)

    # Top arrow: left to right (blue)
    cr.set_source_rgba(0.35, 0.55, 0.85, 0.95)
    y_top = cy - gap
    cr.move_to(cx - arrow_len, y_top)
    cr.line_to(cx + arrow_len, y_top)
    cr.stroke()
    cr.move_to(cx + arrow_len - head, y_top - head)
    cr.line_to(cx + arrow_len, y_top)
    cr.line_to(cx + arrow_len - head, y_top + head)
    cr.stroke()

    # Bottom arrow: right to left (teal)
    cr.set_source_rgba(0.25, 0.75, 0.65, 0.95)
    y_bot = cy + gap
    cr.move_to(cx + arrow_len, y_bot)
    cr.line_to(cx - arrow_len, y_bot)
    cr.stroke()
    cr.move_to(cx - arrow_len + head, y_bot - head)
    cr.line_to(cx - arrow_len, y_bot)
    cr.line_to(cx - arrow_len + head, y_bot + head)
    cr.stroke()

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
