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

"""Dock icon rendering for the Run Application applet."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.draw import rounded_rect


def create_icon(size: int) -> GdkPixbuf.Pixbuf | None:
    """Render a compact command prompt icon."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()

    pad = size * 0.10
    x = pad
    y = size * 0.16
    width = size - 2 * pad
    height = size * 0.68
    radius = size * 0.10

    rounded_rect(cr=cr, x=x, y=y, width=width, height=height, radius=radius)
    cr.set_source_rgb(0.12, 0.13, 0.15)
    cr.fill()

    rounded_rect(
        cr=cr,
        x=x + size * 0.035,
        y=y + size * 0.035,
        width=width - size * 0.07,
        height=height - size * 0.07,
        radius=radius * 0.70,
    )
    cr.set_source_rgb(0.93, 0.95, 0.93)
    cr.fill()

    header_h = height * 0.24
    rounded_rect(cr=cr, x=x, y=y, width=width, height=header_h, radius=radius)
    cr.set_source_rgb(0.45, 0.58, 0.51)
    cr.fill()
    cr.rectangle(x, y + header_h * 0.58, width, header_h * 0.42)
    cr.fill()

    cr.set_source_rgb(0.10, 0.17, 0.16)
    cr.set_line_width(max(1.0, size * 0.065))
    prompt_x = x + width * 0.18
    prompt_y = y + header_h + height * 0.28
    cr.move_to(prompt_x, prompt_y - size * 0.10)
    cr.line_to(prompt_x + size * 0.12, prompt_y)
    cr.line_to(prompt_x, prompt_y + size * 0.10)
    cr.stroke()

    cr.set_line_width(max(1.0, size * 0.055))
    cr.move_to(prompt_x + size * 0.20, prompt_y + size * 0.10)
    cr.line_to(x + width * 0.78, prompt_y + size * 0.10)
    cr.stroke()

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
