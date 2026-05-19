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

"""Rendering and menu item UI helpers for Applications applet."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, Gtk

from docking.applets.draw import rounded_rect

MENU_ICON_PX = 16


def _draw_applications_icon(*, cr: cairo.Context, size: int) -> None:
    """Draw custom folder/app-grid icon matching Dock identity."""
    outline = (0.18, 0.18, 0.50)
    header = (0.53, 0.79, 0.63)
    highlight = (0.60, 0.84, 0.72)
    body = (0.93, 0.93, 0.95)

    x = size * 0.05
    y = size * 0.08
    w = size * 0.90
    h = size * 0.88
    rounded_rect(cr=cr, x=x, y=y, width=w, height=h, radius=size * 0.10)
    cr.set_source_rgb(*outline)
    cr.fill()

    inner = size * 0.02
    ix = x + inner
    iy = y + inner
    iw = w - 2 * inner
    ih = h - 2 * inner
    rounded_rect(cr=cr, x=ix, y=iy, width=iw, height=ih, radius=size * 0.08)
    cr.set_source_rgb(*body)
    cr.fill()

    hh = ih * 0.30
    rounded_rect(cr=cr, x=ix, y=iy, width=iw, height=hh, radius=size * 0.07)
    cr.set_source_rgb(*header)
    cr.fill()

    cr.rectangle(ix, iy, iw * 0.14, hh)
    cr.set_source_rgb(*highlight)
    cr.fill()

    line_y = iy + hh
    cr.set_line_width(max(1.0, size * 0.018))
    cr.set_source_rgb(*outline)
    cr.move_to(ix, line_y)
    cr.line_to(ix + iw, line_y)
    cr.stroke()

    dot_r = size * 0.03
    for dx, color in (
        (ix + iw * 0.10, (0.98, 0.83, 0.00)),
        (ix + iw * 0.24, (0.95, 0.35, 0.41)),
        (ix + iw * 0.38, (0.95, 0.95, 0.96)),
    ):
        dy = iy + hh * 0.42
        cr.arc(dx, dy, dot_r, 0, math.tau)
        cr.set_source_rgb(*outline)
        cr.fill_preserve()
        cr.set_line_width(max(1.0, size * 0.015))
        cr.stroke()
        cr.arc(dx, dy, dot_r * 0.62, 0, math.tau)
        cr.set_source_rgb(*color)
        cr.fill()

    cols = 3
    rows = 3
    gap = iw * 0.08
    cell_by_width = (iw * 0.72 - gap * (cols - 1)) / cols
    cell_by_height = (ih * 0.52 - gap * (rows - 1)) / rows
    cell = min(cell_by_width, cell_by_height)
    grid_w = cols * cell + (cols - 1) * gap
    grid_h = rows * cell + (rows - 1) * gap
    gx = ix + (iw - grid_w) / 2
    gy = line_y + (ih - hh - grid_h) / 2
    palette = [
        (0.96, 0.33, 0.40),
        (0.74, 0.75, 0.84),
        (0.74, 0.75, 0.84),
        (0.97, 0.84, 0.00),
        (0.97, 0.84, 0.00),
        (0.97, 0.84, 0.00),
        (0.37, 0.57, 0.87),
        (0.37, 0.57, 0.87),
        (0.74, 0.75, 0.84),
    ]

    idx = 0
    bright_cells = {(0.96, 0.33, 0.40), (0.97, 0.84, 0.00), (0.37, 0.57, 0.87)}
    for row in range(rows):
        for col in range(cols):
            cx = gx + col * (cell + gap)
            cy = gy + row * (cell + gap)
            rounded_rect(
                cr=cr,
                x=cx,
                y=cy,
                width=cell,
                height=cell,
                radius=size * 0.035,
            )
            cr.set_source_rgb(*outline)
            cr.fill()

            inset = cell * 0.14
            inner_x = cx + inset
            inner_y = cy + inset
            inner_w = cell - 2 * inset
            inner_h = cell - 2 * inset
            color = palette[idx]
            rounded_rect(
                cr=cr,
                x=inner_x,
                y=inner_y,
                width=inner_w,
                height=inner_h,
                radius=size * 0.018,
            )
            cr.set_source_rgb(*color)
            cr.fill()

            if color in bright_cells:
                cr.rectangle(inner_x, inner_y, inner_w * 0.25, inner_h)
                cr.set_source_rgba(1, 1, 1, 0.18)
                cr.fill()
            idx += 1


def create_icon(size: int) -> GdkPixbuf.Pixbuf | None:
    """Render custom applications/folder icon."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()
    _draw_applications_icon(cr=cr, size=size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def normalize_menu_icon(image: Gtk.Image) -> None:
    """Force consistent menu icon size across themes/environments."""
    image.set_pixel_size(MENU_ICON_PX)
    image.set_size_request(MENU_ICON_PX, MENU_ICON_PX)
    image.set_valign(Gtk.Align.CENTER)


def make_menu_item_with_icon(
    label: str,
    icon_name: str | None = None,
    gicon: Gio.Icon | None = None,
) -> Gtk.MenuItem:
    """Create a Gtk.MenuItem with an optional icon using non-deprecated widgets."""
    item = Gtk.MenuItem()
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    row.set_halign(Gtk.Align.START)
    row.set_margin_start(0)
    row.set_margin_end(0)

    image: Gtk.Image | None = None
    if gicon is not None:
        image = Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.MENU)
    elif icon_name:
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)

    if image is not None:
        normalize_menu_icon(image=image)
        image.set_margin_start(0)
        image.set_margin_end(0)
        row.pack_start(image, False, False, 0)

    text = Gtk.Label(label=label)
    text.set_xalign(0.0)
    text.set_margin_start(0)
    row.pack_start(text, False, False, 0)

    item.add(row)
    return item
