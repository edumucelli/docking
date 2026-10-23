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

"""Pure Cairo rendering for Ambient applet icon."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.draw import rounded_rect


def _draw_waveform_icon(cr: cairo.Context, size: int) -> None:
    """Draw purple rounded tile with centered waveform bars."""
    margin = size * 0.09
    x = margin
    y = margin
    w = size - (2 * margin)
    h = w
    radius = 0.13 * w

    # Subtle drop shadow.
    rounded_rect(
        cr=cr,
        x=x + (size * 0.01),
        y=y + (size * 0.018),
        width=w,
        height=h,
        radius=radius,
    )
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.20)
    cr.fill()

    # Tile fill gradient.
    gradient = cairo.LinearGradient(x, y, x + w, y + h)
    gradient.add_color_stop_rgb(0.0, 0xD2 / 255.0, 0x80 / 255.0, 0xB9 / 255.0)
    gradient.add_color_stop_rgb(1.0, 0x89 / 255.0, 0x40 / 255.0, 0xA8 / 255.0)
    rounded_rect(cr=cr, x=x, y=y, width=w, height=h, radius=radius)
    cr.set_source(gradient)
    cr.fill_preserve()

    # Border for crisp edge at smaller sizes.
    cr.set_source_rgba(0.48, 0.20, 0.55, 0.70)
    cr.set_line_width(max(1.0, size * 0.016))
    cr.stroke()

    # Wave bars.
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.88)
    bar_count = 16
    heights = [
        0.30,
        0.55,
        0.75,
        0.88,
        1.00,
        0.84,
        0.54,
        0.34,
        0.26,
        0.34,
        0.54,
        0.84,
        1.00,
        0.75,
        0.55,
        0.30,
    ]

    # 0.76 width ratio centers waveform with padding; 0.024 spacing between bars
    waveform_w = w * 0.76
    spacing = w * 0.024
    total_spacing = spacing * (bar_count - 1)
    bar_w = max(size * 0.01, (waveform_w - total_spacing) / bar_count)
    max_bar_h = h * 0.52
    start_x = x + (w - ((bar_w * bar_count) + total_spacing)) / 2
    center_y = y + (h / 2)

    for i, height in enumerate(heights):
        bar_h = height * max_bar_h
        if i >= bar_count // 2:
            # Asymmetric: shorter right half suggests waveform envelope shape
            bar_h *= 0.84
        bar_x = start_x + i * (bar_w + spacing)
        bar_y = center_y - (bar_h / 2)
        bar_radius = bar_w * 0.35
        rounded_rect(
            cr=cr, x=bar_x, y=bar_y, width=bar_w, height=bar_h, radius=bar_radius
        )
        cr.fill()


def render_icon(size: int) -> GdkPixbuf.Pixbuf | None:
    """Render ambient applet icon."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    _draw_waveform_icon(cr=cr, size=size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
