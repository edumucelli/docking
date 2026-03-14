"""Pure Cairo renderer for Clippy applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf


def _draw_scissors(*, cr: cairo.Context, size: int) -> None:
    """Draw crossed scissors icon inspired by the provided reference."""
    cr.set_antialias(cairo.ANTIALIAS_BEST)

    dark = (0.90, 0.56, 0.16)
    light = (0.90, 0.73, 0.49)
    ring_left = (0.92, 0.57, 0.16)
    ring_right = (0.88, 0.43, 0.19)
    pivot = (0.70, 0.70, 0.70)

    # Blades as rounded bars (prevents faceted/clipped tips).
    blade_w = max(1.0, size * 0.16)
    cr.set_line_width(blade_w)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    # Light blade (top-left to bottom-right)
    cr.move_to(size * 0.30, size * 0.24)
    cr.line_to(size * 0.82, size * 0.88)
    cr.set_source_rgb(*light)
    cr.stroke()

    # Dark blade (top-right to bottom-left)
    cr.move_to(size * 0.69, size * 0.24)
    cr.line_to(size * 0.18, size * 0.88)
    cr.set_source_rgb(*dark)
    cr.stroke()

    # Handle rings
    ring_r = size * 0.18
    hole_rx = size * 0.078
    hole_ry = size * 0.096

    left_cx = size * 0.18
    left_cy = size * 0.18
    right_cx = size * 0.82
    right_cy = size * 0.18

    cr.arc(left_cx, left_cy, ring_r, 0, math.tau)
    cr.set_source_rgb(*ring_left)
    cr.fill()
    cr.arc(left_cx, left_cy, ring_r, 0, math.tau)
    cr.set_source_rgba(0, 0, 0, 0.12)
    cr.set_line_width(max(1.0, size * 0.012))
    cr.stroke()

    cr.arc(right_cx, right_cy, ring_r, 0, math.tau)
    cr.set_source_rgb(*ring_right)
    cr.fill()
    cr.arc(right_cx, right_cy, ring_r, 0, math.tau)
    cr.set_source_rgba(0, 0, 0, 0.12)
    cr.set_line_width(max(1.0, size * 0.012))
    cr.stroke()

    # Punch transparent finger holes (slightly oval, as in the reference icon)
    cr.save()
    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.translate(left_cx, left_cy)
    cr.rotate(math.radians(-18))
    cr.scale(hole_rx, hole_ry)
    cr.arc(0, 0, 1, 0, math.tau)
    cr.fill()
    cr.restore()

    cr.save()
    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.translate(right_cx, right_cy)
    cr.rotate(math.radians(16))
    cr.scale(hole_rx, hole_ry)
    cr.arc(0, 0, 1, 0, math.tau)
    cr.fill()
    cr.restore()

    # Pivot
    cr.arc(size * 0.50, size * 0.50, size * 0.055, 0, math.tau)
    cr.set_source_rgb(*pivot)
    cr.fill()


def create_icon(size: int) -> GdkPixbuf.Pixbuf | None:
    """Render custom Clippy scissors icon."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()
    _draw_scissors(cr=cr, size=size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
