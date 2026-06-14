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

"""Cairo rendering for the Drag Share applet."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.dragshare.state import DragshareStatus

_STATUS_COLORS: dict[DragshareStatus, tuple[float, float, float]] = {
    DragshareStatus.IDLE: (0.26, 0.55, 0.95),
    DragshareStatus.UPLOADING: (0.96, 0.66, 0.18),
    DragshareStatus.DONE: (0.22, 0.72, 0.42),
    DragshareStatus.ERROR: (0.90, 0.24, 0.24),
}


def render_icon(
    size: int,
    status: DragshareStatus = DragshareStatus.IDLE,
) -> GdkPixbuf.Pixbuf | None:
    """Render a drop/upload tray icon tinted by current status."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    accent = _STATUS_COLORS[status]
    _draw_tray(cr=cr, size=size, accent=accent)
    _draw_arrow(cr=cr, size=size, accent=accent)

    if status is DragshareStatus.DONE:
        _draw_check(cr=cr, size=size)
    elif status is DragshareStatus.ERROR:
        _draw_error(cr=cr, size=size)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _draw_tray(
    *, cr: cairo.Context, size: int, accent: tuple[float, float, float]
) -> None:
    x = size * 0.18
    y = size * 0.58
    w = size * 0.64
    h = size * 0.22
    r = size * 0.08

    cr.set_source_rgba(0.06, 0.08, 0.12, 0.45)
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.arc(x + w - r, y + r, r, 1.5 * math.pi, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 0.5 * math.pi)
    cr.arc(x + r, y + h - r, r, 0.5 * math.pi, math.pi)
    cr.close_path()
    cr.fill()

    cr.set_source_rgba(accent[0], accent[1], accent[2], 0.90)
    cr.set_line_width(max(1.5, size * 0.055))
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.move_to(x, y + h * 0.35)
    cr.line_to(x, y + h)
    cr.line_to(x + w, y + h)
    cr.line_to(x + w, y + h * 0.35)
    cr.stroke()


def _draw_arrow(
    *, cr: cairo.Context, size: int, accent: tuple[float, float, float]
) -> None:
    cx = size / 2
    top = size * 0.18
    bottom = size * 0.60
    head = size * 0.15

    cr.set_source_rgba(accent[0], accent[1], accent[2], 0.98)
    cr.set_line_width(max(2.0, size * 0.09))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.move_to(cx, bottom)
    cr.line_to(cx, top + head * 0.45)
    cr.stroke()

    cr.set_line_width(max(2.0, size * 0.075))
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.move_to(cx - head, top + head)
    cr.line_to(cx, top)
    cr.line_to(cx + head, top + head)
    cr.stroke()


def _draw_check(*, cr: cairo.Context, size: int) -> None:
    cr.set_source_rgba(1, 1, 1, 0.95)
    cr.set_line_width(max(1.4, size * 0.045))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.move_to(size * 0.60, size * 0.29)
    cr.line_to(size * 0.67, size * 0.36)
    cr.line_to(size * 0.80, size * 0.20)
    cr.stroke()


def _draw_error(*, cr: cairo.Context, size: int) -> None:
    cr.set_source_rgba(1, 1, 1, 0.95)
    cr.set_line_width(max(1.4, size * 0.045))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.move_to(size * 0.63, size * 0.21)
    cr.line_to(size * 0.78, size * 0.36)
    cr.move_to(size * 0.78, size * 0.21)
    cr.line_to(size * 0.63, size * 0.36)
    cr.stroke()
