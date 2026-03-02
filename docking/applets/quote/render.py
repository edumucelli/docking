"""Rendering helpers for the Quote applet icon."""

from __future__ import annotations

import math

import cairo

TWO_PI = 2 * math.pi


def draw_bulb_icon(*, cr: cairo.Context, size: int) -> None:
    cx = size / 2
    bulb_r = size * 0.34
    bulb_cy = size * 0.36

    # Bulb glass
    cr.arc(cx, bulb_cy, bulb_r, 0, TWO_PI)
    cr.set_source_rgba(1.0, 0.87, 0.20, 0.96)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.9)
    cr.set_line_width(max(1.4, size * 0.045))
    cr.stroke()

    # Neck
    neck_w = size * 0.22
    neck_h = size * 0.12
    neck_x = cx - neck_w / 2
    neck_y = bulb_cy + bulb_r * 0.62
    rounded_rect(cr=cr, x=neck_x, y=neck_y, w=neck_w, h=neck_h, r=size * 0.03)
    cr.set_source_rgba(0.92, 0.78, 0.18, 0.98)
    cr.fill()

    # Base
    base_w = size * 0.30
    base_h = size * 0.21
    base_x = cx - base_w / 2
    base_y = neck_y + neck_h - size * 0.01
    rounded_rect(
        cr=cr,
        x=base_x,
        y=base_y,
        w=base_w,
        h=base_h,
        r=size * 0.035,
    )
    cr.set_source_rgba(0.33, 0.35, 0.40, 0.97)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.25)
    cr.set_line_width(max(1.0, size * 0.03))
    cr.stroke()

    # Base grooves
    cr.set_source_rgba(1, 1, 1, 0.35)
    cr.set_line_width(max(1.0, size * 0.02))
    for i in range(3):
        y = base_y + base_h * (0.28 + i * 0.22)
        cr.move_to(base_x + size * 0.02, y)
        cr.line_to(base_x + base_w - size * 0.02, y)
        cr.stroke()

    # Highlight
    cr.arc(cx - bulb_r * 0.35, bulb_cy - bulb_r * 0.25, bulb_r * 0.32, 0, TWO_PI)
    cr.set_source_rgba(1, 1, 1, 0.22)
    cr.fill()


def rounded_rect(
    *,
    cr: cairo.Context,
    x: float,
    y: float,
    w: float,
    h: float,
    r: float,
) -> None:
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
