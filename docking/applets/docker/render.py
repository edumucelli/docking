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

"""Pure Cairo rendering for the Docker applet icon."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label
from docking.applets.draw import rounded_rect

_BLUE = (0.06, 0.48, 0.82)
_BLUE_DARK = (0.03, 0.20, 0.38)
_BLUE_LIGHT = (0.14, 0.63, 0.94)
_WHITE = (0.95, 0.98, 1.0)
_UNAVAILABLE = (0.45, 0.48, 0.50)


def render_icon(
    *,
    size: int,
    running_count: int,
    available: bool,
) -> GdkPixbuf.Pixbuf | None:
    """Render a Docker-style container stack with the running count."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    _draw_whale(cr=cr, size=size, available=available)
    _draw_container_stack(cr=cr, size=size, available=available)

    if available and running_count > 0:
        draw_icon_label(
            cr=cr,
            text=str(running_count),
            size=size,
            max_width=size * 0.36,
            fill_rgba=(1, 1, 1, 1),
            outline_rgba=(0.0, 0.13, 0.25, 0.88),
        )

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _draw_whale(*, cr: cairo.Context, size: int, available: bool) -> None:
    color = _BLUE if available else _UNAVAILABLE
    dark = (0.05, 0.28, 0.63) if available else (0.25, 0.27, 0.28)

    cr.save()
    cr.move_to(size * 0.02, size * 0.53)
    cr.curve_to(
        size * 0.04,
        size * 0.49,
        size * 0.18,
        size * 0.50,
        size * 0.31,
        size * 0.50,
    )
    cr.curve_to(
        size * 0.54,
        size * 0.50,
        size * 0.68,
        size * 0.53,
        size * 0.77,
        size * 0.46,
    )
    cr.curve_to(
        size * 0.82,
        size * 0.35,
        size * 0.81,
        size * 0.25,
        size * 0.88,
        size * 0.18,
    )
    cr.curve_to(
        size * 0.95,
        size * 0.23,
        size * 0.98,
        size * 0.31,
        size * 0.98,
        size * 0.40,
    )
    cr.curve_to(
        size * 0.91,
        size * 0.37,
        size * 0.85,
        size * 0.37,
        size * 0.80,
        size * 0.43,
    )
    cr.curve_to(
        size * 0.88,
        size * 0.39,
        size * 0.95,
        size * 0.40,
        size * 1.01,
        size * 0.45,
    )
    cr.curve_to(
        size * 0.96,
        size * 0.53,
        size * 0.89,
        size * 0.57,
        size * 0.78,
        size * 0.54,
    )
    cr.curve_to(
        size * 0.66,
        size * 0.80,
        size * 0.34,
        size * 0.96,
        size * 0.08,
        size * 0.87,
    )
    cr.curve_to(
        size * 0.03,
        size * 0.76,
        size * 0.00,
        size * 0.62,
        size * 0.02,
        size * 0.53,
    )
    cr.close_path()
    cr.set_source_rgb(*color)
    cr.fill_preserve()
    cr.set_source_rgb(*dark)
    cr.set_line_width(max(1.0, size * 0.028))
    cr.stroke()

    cr.move_to(size * 0.09, size * 0.86)
    cr.curve_to(
        size * 0.19,
        size * 0.94,
        size * 0.36,
        size * 0.98,
        size * 0.43,
        size * 0.94,
    )
    cr.curve_to(
        size * 0.34,
        size * 0.89,
        size * 0.29,
        size * 0.79,
        size * 0.25,
        size * 0.73,
    )
    cr.curve_to(
        size * 0.19,
        size * 0.80,
        size * 0.15,
        size * 0.83,
        size * 0.09,
        size * 0.86,
    )
    cr.close_path()
    cr.set_source_rgb(*_WHITE)
    cr.fill()

    cr.move_to(size * 0.08, size * 0.85)
    cr.curve_to(
        size * 0.15,
        size * 0.87,
        size * 0.23,
        size * 0.83,
        size * 0.28,
        size * 0.79,
    )
    cr.set_source_rgb(*dark)
    cr.set_line_width(max(1.2, size * 0.018))
    cr.set_line_cap(cairo.LineCap.ROUND)
    cr.stroke()

    cr.set_source_rgb(*_WHITE)
    cr.arc(size * 0.34, size * 0.70, size * 0.045, 0, 2 * 3.14159)
    cr.fill()
    cr.set_source_rgb(*dark)
    cr.arc(size * 0.35, size * 0.69, size * 0.024, 0, 2 * 3.14159)
    cr.fill()
    cr.set_source_rgb(*_WHITE)
    cr.arc(size * 0.36, size * 0.67, size * 0.010, 0, 2 * 3.14159)
    cr.fill()
    cr.restore()


def _draw_container_stack(*, cr: cairo.Context, size: int, available: bool) -> None:
    fill = _BLUE_LIGHT if available else (0.62, 0.64, 0.66)
    stroke = _BLUE_DARK if available else (0.28, 0.30, 0.31)
    x0 = size * 0.20
    y0 = size * 0.24
    box = size * 0.15
    gap = size * 0.018

    rows = (
        (1, 0),
        (0, 1),
        (1, 1),
        (2, 1),
    )
    for col, row in rows:
        x = x0 + col * (box + gap)
        y = y0 + row * (box + gap)
        rounded_rect(
            cr=cr,
            x=x,
            y=y,
            width=box,
            height=box,
            radius=max(1.0, size * 0.018),
        )
        cr.set_source_rgb(*fill)
        cr.fill_preserve()
        cr.set_source_rgb(*stroke)
        cr.set_line_width(max(0.8, size * 0.012))
        cr.stroke()

        cr.set_source_rgba(*_WHITE, 0.38)
        cr.rectangle(x + box * 0.18, y + box * 0.18, box * 0.42, box * 0.16)
        cr.fill()
