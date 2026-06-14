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

"""Rendering helpers for the System Tray applet."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.draw import rounded_rect
from docking.ui.overlays import draw_count_badge


def create_status_tray_icon(
    *,
    size: int,
    available: bool,
    item_count: int,
) -> GdkPixbuf.Pixbuf | None:
    """Render a serving-tray host icon with an item-count badge."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    _draw_cloche(cr=cr, size=size, available=available)

    if available and item_count > 0:
        badge_size = size * 0.34
        draw_count_badge(
            cr=cr,
            x=size - badge_size - size * 0.03,
            y=size - badge_size - size * 0.02,
            width=badge_size,
            height=badge_size,
            badge_count=item_count,
        )

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _draw_cloche(*, cr: cairo.Context, size: int, available: bool) -> None:
    stroke = max(2.0, size * 0.095)
    cr.set_line_width(stroke)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    if available:
        dome_color = (0.94, 0.27, 0.30, 0.98)
        lip_color = (0.37, 0.82, 0.81, 0.98)
        outline = (0.18, 0.18, 0.17, 0.98)
        shine = (1.0, 0.96, 0.92, 0.90)
    else:
        dome_color = (0.45, 0.46, 0.48, 0.92)
        lip_color = (0.58, 0.60, 0.62, 0.92)
        outline = (0.24, 0.25, 0.26, 0.96)
        shine = (0.90, 0.91, 0.92, 0.70)

    left = size * 0.13
    right = size * 0.87
    base_y = size * 0.70
    top_y = size * 0.24
    cx = size * 0.50

    cr.new_path()
    cr.move_to(left, base_y)
    cr.curve_to(left, size * 0.45, size * 0.30, top_y, cx, top_y)
    cr.curve_to(size * 0.70, top_y, right, size * 0.45, right, base_y)
    cr.close_path()
    cr.set_source_rgba(*dome_color)
    cr.fill_preserve()
    cr.set_source_rgba(*outline)
    cr.stroke()

    handle_w = size * 0.22
    handle_h = size * 0.15
    handle_x = cx - handle_w / 2
    handle_y = size * 0.11
    cr.new_path()
    cr.move_to(handle_x, top_y)
    cr.line_to(handle_x, handle_y + handle_h)
    cr.curve_to(handle_x, handle_y, handle_x, handle_y, cx, handle_y)
    cr.curve_to(
        handle_x + handle_w,
        handle_y,
        handle_x + handle_w,
        handle_y,
        handle_x + handle_w,
        handle_y + handle_h,
    )
    cr.line_to(handle_x + handle_w, top_y)
    cr.set_source_rgba(*dome_color)
    cr.fill_preserve()
    cr.set_source_rgba(*outline)
    cr.stroke()

    slot_w = size * 0.13
    slot_h = size * 0.05
    rounded_rect(
        cr=cr,
        x=cx - slot_w / 2,
        y=handle_y + size * 0.04,
        width=slot_w,
        height=slot_h,
        radius=slot_h / 2,
    )
    cr.set_source_rgba(*outline)
    cr.fill()

    base_h = size * 0.13
    base_x = size * 0.08
    base_w = size * 0.84
    rounded_rect(
        cr=cr,
        x=base_x,
        y=base_y - base_h * 0.30,
        width=base_w,
        height=base_h,
        radius=base_h * 0.35,
    )
    cr.set_source_rgba(*outline)
    cr.fill()
    inset = stroke * 0.45
    rounded_rect(
        cr=cr,
        x=base_x + inset,
        y=base_y + base_h * 0.02,
        width=base_w - inset * 2,
        height=base_h * 0.42,
        radius=base_h * 0.18,
    )
    cr.set_source_rgba(*lip_color)
    cr.fill()

    cr.set_source_rgba(*shine)
    cr.set_line_width(max(1.6, size * 0.055))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.move_to(size * 0.23, size * 0.58)
    cr.curve_to(
        size * 0.26, size * 0.46, size * 0.34, size * 0.35, size * 0.44, size * 0.31
    )
    cr.stroke()
    cr.set_line_width(max(1.3, size * 0.04))
    cr.move_to(size * 0.48, size * 0.30)
    cr.line_to(size * 0.56, size * 0.29)
    cr.stroke()
