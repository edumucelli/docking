"""Rendering helpers for session applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

TWO_PI = 2 * math.pi


def create_session_icon(*, size: int) -> GdkPixbuf.Pixbuf | None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    cx = size / 2
    cy = size / 2
    stroke = (0.23, 0.58, 0.98)
    fill = (0.68, 0.80, 0.97)
    ring_r = size * 0.42

    # Filled circular background.
    cr.set_source_rgb(*fill)
    cr.arc(cx, cy, ring_r, 0, TWO_PI)
    cr.fill()

    # Outer ring (thinner stroke).
    cr.set_source_rgb(*stroke)
    cr.set_line_width(max(1.2, size * 0.05))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.arc(cx, cy, ring_r, 0, TWO_PI)
    cr.stroke()

    # Head.
    cr.arc(cx, cy - size * 0.11, size * 0.12, 0, TWO_PI)
    cr.set_source_rgb(0.79, 0.88, 0.99)
    cr.fill_preserve()
    cr.set_source_rgb(*stroke)
    cr.set_line_width(max(1.2, size * 0.03))
    cr.stroke()

    # Shoulders / torso curve connected to the outer ring.
    left_angle = math.radians(145)
    right_angle = math.radians(35)
    left_x = cx + ring_r * math.cos(left_angle)
    left_y = cy + ring_r * math.sin(left_angle)
    right_x = cx + ring_r * math.cos(right_angle)
    right_y = cy + ring_r * math.sin(right_angle)

    cr.set_source_rgb(*stroke)
    cr.set_line_width(max(1.8, size * 0.055))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.move_to(left_x, left_y)
    cr.curve_to(
        cx - size * 0.16,
        cy + size * 0.03,
        cx + size * 0.16,
        cy + size * 0.03,
        right_x,
        right_y,
    )
    cr.stroke()

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
