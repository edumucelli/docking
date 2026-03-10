"""Rendering helpers for the Quote applet icon."""

from __future__ import annotations

import math

import cairo

from docking.applets.draw import rounded_rect


def draw_bulb_icon(*, cr: cairo.Context, size: int) -> None:
    cx = size / 2
    bulb_r = size * 0.34
    bulb_cy = size * 0.36

    # Bulb glass
    cr.arc(cx, bulb_cy, bulb_r, 0, math.tau)
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
    rounded_rect(
        cr=cr, x=neck_x, y=neck_y, width=neck_w, height=neck_h, radius=size * 0.03
    )
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
        width=base_w,
        height=base_h,
        radius=size * 0.035,
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
    cr.arc(cx - bulb_r * 0.35, bulb_cy - bulb_r * 0.25, bulb_r * 0.32, 0, math.tau)
    cr.set_source_rgba(1, 1, 1, 0.22)
    cr.fill()
