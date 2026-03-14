"""Rendering helpers for the Today in History applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

from docking.applets.draw import rounded_rect


def draw_today_in_history_icon(*, cr: cairo.Context, size: int) -> None:
    card_x = size * 0.16
    card_y = size * 0.12
    card_w = size * 0.68
    card_h = size * 0.76
    radius = size * 0.10

    rounded_rect(
        cr=cr,
        x=card_x,
        y=card_y,
        width=card_w,
        height=card_h,
        radius=radius,
    )
    cr.set_source_rgba(0.97, 0.95, 0.90, 0.98)
    cr.fill_preserve()
    cr.set_source_rgba(0.37, 0.30, 0.18, 0.85)
    cr.set_line_width(max(1.4, size * 0.035))
    cr.stroke()

    header_h = card_h * 0.24
    rounded_rect(
        cr=cr,
        x=card_x,
        y=card_y,
        width=card_w,
        height=header_h,
        radius=radius,
    )
    cr.set_source_rgba(0.80, 0.24, 0.20, 0.98)
    cr.fill()

    ring_y = card_y + header_h * 0.22
    for center_x in (card_x + card_w * 0.28, card_x + card_w * 0.72):
        cr.arc(center_x, ring_y, size * 0.038, 0, math.tau)
        cr.set_source_rgba(0.91, 0.88, 0.83, 1.0)
        cr.fill()

    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_source_rgba(0.12, 0.48, 0.63, 0.96)
    cr.set_line_width(max(2.0, size * 0.06))
    cr.arc(
        size * 0.50,
        size * 0.52,
        size * 0.17,
        math.radians(40),
        math.radians(330),
    )
    cr.stroke()

    arrow_x = size * 0.62
    arrow_y = size * 0.39
    cr.move_to(arrow_x, arrow_y)
    cr.line_to(arrow_x + size * 0.05, arrow_y)
    cr.line_to(arrow_x + size * 0.02, arrow_y + size * 0.045)
    cr.close_path()
    cr.set_source_rgba(0.12, 0.48, 0.63, 0.96)
    cr.fill()

    cr.set_source_rgba(0.37, 0.30, 0.18, 0.92)
    cr.set_line_width(max(1.6, size * 0.045))
    cr.move_to(size * 0.50, size * 0.52)
    cr.line_to(size * 0.50, size * 0.42)
    cr.move_to(size * 0.50, size * 0.52)
    cr.line_to(size * 0.43, size * 0.58)
    cr.stroke()

    cr.arc(size * 0.50, size * 0.52, size * 0.03, 0, math.tau)
    cr.set_source_rgba(0.95, 0.71, 0.25, 0.98)
    cr.fill()

    cr.set_source_rgba(0.52, 0.44, 0.30, 0.28)
    cr.set_line_width(max(1.0, size * 0.02))
    for y in (0.73, 0.81):
        cr.move_to(card_x + size * 0.10, size * y)
        cr.line_to(card_x + card_w - size * 0.10, size * y)
        cr.stroke()


def render_icon(size: int) -> GdkPixbuf.Pixbuf | None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    draw_today_in_history_icon(cr=cr, size=size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
