"""Cairo rendering for Bluetooth applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf


def create_bluetooth_icon(
    *,
    size: int,
    available: bool,
    powered: bool,
    discovering: bool,
    connected_devices: int,
) -> GdkPixbuf.Pixbuf | None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    cx = size * 0.5
    cy = size * 0.5
    radius = size * 0.40
    cr.arc(cx, cy, radius, 0, math.tau)

    if not available:
        cr.set_source_rgba(0.42, 0.42, 0.44, 0.95)
    elif not powered:
        cr.set_source_rgba(0.30, 0.33, 0.38, 0.95)
    else:
        cr.set_source_rgba(0.16, 0.53, 0.92, 0.96)
    cr.fill()

    cr.arc(cx, cy, radius, 0, math.tau)
    cr.set_source_rgba(1, 1, 1, 0.18)
    cr.set_line_width(max(1.0, size * 0.018))
    cr.stroke()

    if discovering and available and powered:
        cr.arc(cx, cy, size * 0.43, 0, math.tau)
        cr.set_source_rgba(1, 1, 1, 0.28)
        cr.set_line_width(max(1.2, size * 0.03))
        cr.stroke()

    _draw_bluetooth_glyph(cr=cr, size=size, available=available)
    _draw_connected_badge(cr=cr, size=size, count=connected_devices)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _draw_bluetooth_glyph(
    *,
    cr: cairo.Context,
    size: int,
    available: bool,
) -> None:
    stem_x = size * 0.47
    top = size * 0.26
    bottom = size * 0.76
    mid = (top + bottom) / 2.0
    right = size * 0.65
    upper_mid = size * 0.41
    lower_mid = size * 0.61

    if available:
        cr.set_source_rgba(1, 1, 1, 0.95)
    else:
        cr.set_source_rgba(0.93, 0.93, 0.95, 0.88)

    cr.set_line_width(max(1.5, size * 0.050))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)

    # Vertical stem.
    cr.move_to(stem_x, top)
    cr.line_to(stem_x, bottom)

    # Upper branch.
    cr.move_to(stem_x, top)
    cr.line_to(right, upper_mid)
    cr.line_to(stem_x, mid)

    # Lower branch (includes return to stem at bottom).
    cr.move_to(stem_x, mid)
    cr.line_to(right, lower_mid)
    cr.line_to(stem_x, bottom)
    cr.stroke()


def _draw_connected_badge(*, cr: cairo.Context, size: int, count: int) -> None:
    if count <= 0:
        return

    radius = size * 0.16
    cx = size - radius - size * 0.06
    cy = size - radius - size * 0.06

    cr.arc(cx, cy, radius, 0, math.tau)
    cr.set_source_rgba(0.20, 0.75, 0.43, 0.98)
    cr.fill()

    text = "99+" if count > 99 else str(count)
    if count <= 9:
        font_size = size * 0.20
    elif count <= 99:
        font_size = size * 0.15
    else:
        font_size = size * 0.11

    cr.set_source_rgba(1, 1, 1, 0.98)
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(max(7, font_size))
    ext = cr.text_extents(text)
    cr.move_to(
        cx - (ext.width / 2 + ext.x_bearing),
        cy - (ext.height / 2 + ext.y_bearing),
    )
    cr.show_text(text)
