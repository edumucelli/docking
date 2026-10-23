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

"""Rendering helpers for the Devices applet."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf


def create_devices_icon(*, size: int, device_count: int) -> GdkPixbuf.Pixbuf | None:
    """Render a storage device with an optional mounted-device badge."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    active = device_count > 0
    fill = (0.18, 0.38, 0.62) if active else (0.35, 0.39, 0.44)
    x = size * 0.16
    y = size * 0.20
    width = size * 0.68
    height = size * 0.58
    radius = size * 0.09

    _rounded_rect(cr=cr, x=x, y=y, width=width, height=height, radius=radius)
    cr.set_source_rgb(*fill)
    cr.fill_preserve()
    cr.set_source_rgba(0.92, 0.96, 1.0, 0.9)
    cr.set_line_width(max(1.4, size * 0.035))
    cr.stroke()

    cr.set_source_rgba(0.06, 0.12, 0.18, 0.48)
    cr.rectangle(size * 0.23, size * 0.32, size * 0.54, size * 0.08)
    cr.fill()

    cr.set_source_rgb(0.78, 0.93, 1.0)
    cr.arc(size * 0.68, size * 0.64, size * 0.045, 0, math.tau)
    cr.fill()

    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(max(1.5, size * 0.04))
    cr.move_to(size * 0.27, size * 0.65)
    cr.line_to(size * 0.52, size * 0.65)
    cr.set_source_rgba(0.92, 0.96, 1.0, 0.82)
    cr.stroke()

    if device_count > 0:
        badge_r = size * 0.16
        cx = size * 0.76
        cy = size * 0.76
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


def _rounded_rect(
    *,
    cr: cairo.Context,
    x: float,
    y: float,
    width: float,
    height: float,
    radius: float,
) -> None:
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _draw_centered_label(
    *,
    cr: cairo.Context,
    text: str,
    cx: float,
    cy: float,
    font_size: float,
) -> None:
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(font_size)
    xb, _yb, width, _height, _xa, _ya = cr.text_extents(text)
    ascent, descent, _height, _max_x_advance, _max_y_advance = cr.font_extents()
    cr.set_source_rgb(1.0, 1.0, 1.0)
    cr.move_to(cx - width / 2 - xb, cy + (ascent - descent) / 2)
    cr.show_text(text)
