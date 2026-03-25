"""Icon rendering for Calculator applet."""

from __future__ import annotations

import cairo
from gi.repository import Gdk, GdkPixbuf


def create_icon(size: int) -> GdkPixbuf.Pixbuf | None:
    """Render a calculator icon: rounded rectangle with button grid."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    pad = size * 0.12
    w = size - 2 * pad
    h = size - 2 * pad
    r = size * 0.1

    # Body: rounded rectangle
    cr.new_sub_path()
    cr.arc(pad + w - r, pad + r, r, -1.5708, 0)
    cr.arc(pad + w - r, pad + h - r, r, 0, 1.5708)
    cr.arc(pad + r, pad + h - r, r, 1.5708, 3.1416)
    cr.arc(pad + r, pad + r, r, 3.1416, 4.7124)
    cr.close_path()
    cr.set_source_rgba(0.85, 0.85, 0.85, 0.9)
    cr.fill()

    # Display bar
    disp_x = pad + w * 0.12
    disp_y = pad + h * 0.1
    disp_w = w * 0.76
    disp_h = h * 0.2
    cr.rectangle(disp_x, disp_y, disp_w, disp_h)
    cr.set_source_rgba(0.2, 0.25, 0.2, 0.9)
    cr.fill()

    # Button grid: 3x4 dots
    cr.set_source_rgba(0.4, 0.4, 0.4, 0.9)
    btn_top = pad + h * 0.42
    btn_left = pad + w * 0.18
    btn_cols = 4
    btn_rows = 3
    btn_gap_x = w * 0.2
    btn_gap_y = h * 0.15
    btn_r = size * 0.04

    for row in range(btn_rows):
        for col in range(btn_cols):
            bx = btn_left + col * btn_gap_x
            by = btn_top + row * btn_gap_y
            cr.arc(bx, by, btn_r, 0, 6.2832)
            cr.fill()

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
