# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Rendering helpers for the USB Watch applet."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf


def create_usbwatch_icon(*, size: int, device_count: int) -> GdkPixbuf.Pixbuf | None:
    """Render a USB connector icon with an optional mounted-device badge."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    active = device_count > 0
    stroke = (0.16, 0.35, 0.58) if active else (0.36, 0.40, 0.45)

    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_source_rgb(*stroke)

    line_width = max(2.0, size * 0.075)

    # Standard USB glyph: vertical stem, left circle branch, right square branch.
    cr.set_line_width(line_width)
    cr.move_to(size * 0.50, size * 0.82)
    cr.line_to(size * 0.50, size * 0.27)
    cr.stroke()

    cr.move_to(size * 0.50, size * 0.68)
    cr.line_to(size * 0.34, size * 0.52)
    cr.line_to(size * 0.34, size * 0.40)
    cr.stroke()

    cr.move_to(size * 0.50, size * 0.58)
    cr.line_to(size * 0.66, size * 0.42)
    cr.line_to(size * 0.66, size * 0.34)
    cr.stroke()

    # Arrow head, circle endpoint, and square endpoint.
    cr.move_to(size * 0.50, size * 0.12)
    cr.line_to(size * 0.39, size * 0.32)
    cr.line_to(size * 0.61, size * 0.32)
    cr.close_path()
    cr.fill()

    cr.arc(size * 0.34, size * 0.36, size * 0.075, 0, math.tau)
    cr.fill()
    cr.rectangle(size * 0.595, size * 0.235, size * 0.13, size * 0.13)
    cr.fill()

    # Rounded tail cap.
    cr.arc(size * 0.50, size * 0.84, line_width / 2, 0, math.tau)
    cr.fill()

    if device_count > 0:
        badge_r = size * 0.16
        cx = size * 0.73
        cy = size * 0.73
        cr.arc(cx, cy, badge_r, 0, math.tau)
        cr.set_source_rgb(0.08, 0.55, 0.32)
        cr.fill_preserve()
        cr.set_source_rgb(0.91, 0.98, 0.94)
        cr.set_line_width(max(1.2, size * 0.025))
        cr.stroke()

        _draw_centered_label(
            cr=cr,
            text=str(min(device_count, 9)),
            cx=cx,
            cy=cy,
            font_size=size * 0.19,
        )

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _draw_centered_label(
    *,
    cr: cairo.Context,
    text: str,
    cx: float,
    cy: float,
    font_size: float,
) -> None:
    cr.select_font_face(
        "Sans",
        cairo.FONT_SLANT_NORMAL,
        cairo.FONT_WEIGHT_BOLD,
    )
    cr.set_font_size(font_size)
    xb, _yb, width, _height, _xa, _ya = cr.text_extents(text)
    ascent, descent, _height, _max_x_advance, _max_y_advance = cr.font_extents()
    cr.set_source_rgb(1.0, 1.0, 1.0)
    cr.move_to(
        cx - width / 2 - xb,
        cy + (ascent - descent) / 2,
    )
    cr.show_text(text)
