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

"""Pure Cairo rendering for Calendar applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Pango, PangoCairo

from docking.applets.calendar.state import CalendarSnapshot


def _render_calendar_icon(
    cr: cairo.Context,
    size: int,
    day: int,
    weekday: str,
) -> None:
    """Draw a calendar page icon with day number and weekday abbreviation."""
    margin = size * 0.08
    body_x = margin
    body_y = margin
    body_w = size - 2 * margin
    body_h = size - 2 * margin
    radius = size * 0.1
    header_h = body_h * 0.3

    # Body (white rounded rect)
    cr.new_sub_path()
    cr.arc(body_x + body_w - radius, body_y + radius, radius, -math.pi / 2, 0)
    cr.arc(body_x + body_w - radius, body_y + body_h - radius, radius, 0, math.pi / 2)
    cr.arc(body_x + radius, body_y + body_h - radius, radius, math.pi / 2, math.pi)
    cr.arc(body_x + radius, body_y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.set_source_rgba(0.95, 0.95, 0.95, 1)
    cr.fill()

    # Red header bar
    cr.new_sub_path()
    cr.arc(body_x + body_w - radius, body_y + radius, radius, -math.pi / 2, 0)
    cr.line_to(body_x + body_w, body_y + header_h)
    cr.line_to(body_x, body_y + header_h)
    cr.arc(body_x + radius, body_y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.set_source_rgba(0.85, 0.18, 0.18, 1)
    cr.fill()

    # Weekday text in header
    weekday_font_size = max(1, int(header_h * 0.55))
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(
        Pango.FontDescription.from_string(f"Sans Bold {weekday_font_size}px")
    )
    layout.set_text(weekday.upper(), -1)
    _, logical = layout.get_pixel_extents()
    tx = body_x + (body_w - logical.width) / 2 - logical.x
    ty = body_y + (header_h - logical.height) / 2 - logical.y
    cr.move_to(tx, ty)
    cr.set_source_rgba(1, 1, 1, 1)
    PangoCairo.show_layout(cr, layout)

    # Day number in body
    day_area_h = body_h - header_h
    day_font_size = max(1, int(day_area_h * 0.65))
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(
        Pango.FontDescription.from_string(f"Sans Bold {day_font_size}px")
    )
    layout.set_text(str(day), -1)
    _, logical = layout.get_pixel_extents()
    tx = body_x + (body_w - logical.width) / 2 - logical.x
    ty = body_y + header_h + (day_area_h - logical.height) / 2 - logical.y
    cr.move_to(tx, ty)
    cr.set_source_rgba(0.15, 0.15, 0.15, 1)
    PangoCairo.show_layout(cr, layout)


def render_icon(size: int, snapshot: CalendarSnapshot) -> GdkPixbuf.Pixbuf | None:
    """Render calendar icon for current snapshot."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    _render_calendar_icon(
        cr=cr,
        size=size,
        day=snapshot.day,
        weekday=snapshot.weekday,
    )
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
