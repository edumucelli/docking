"""Pure Cairo rendering for keyboard layout applet icon."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

from docking.applets.base import draw_icon_label
from docking.applets.draw import rounded_rect


def _draw_keyboard_base(cr: cairo.Context, size: int) -> None:
    """Draw a stylized keyboard shape."""
    pad = size * 0.12
    body_w = size - 2 * pad
    body_h = size * 0.50
    body_y = size * 0.18

    # Keyboard body
    rounded_rect(
        cr=cr,
        x=pad,
        y=body_y,
        width=body_w,
        height=body_h,
        radius=size * 0.08,
    )
    cr.set_source_rgb(0.25, 0.30, 0.40)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.15)
    cr.set_line_width(max(1.0, size * 0.02))
    cr.stroke()

    # Key rows (3 rows of small squares)
    key_pad = size * 0.03
    row_h = (body_h - 4 * key_pad) / 3
    for row in range(3):
        row_y = body_y + key_pad + row * (row_h + key_pad)
        cols = 6 if row < 2 else 4
        key_w = (body_w - (cols + 1) * key_pad) / cols
        start_x = pad + key_pad + (0 if row < 2 else key_pad + key_w / 2)
        for col in range(cols):
            kx = start_x + col * (key_w + key_pad)
            rounded_rect(
                cr=cr,
                x=kx,
                y=row_y,
                width=key_w,
                height=row_h,
                radius=size * 0.02,
            )
            cr.set_source_rgba(1, 1, 1, 0.12)
            cr.fill()


def render_icon(size: int, label: str) -> GdkPixbuf.Pixbuf | None:
    """Render keyboard layout icon with the layout code label."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    _draw_keyboard_base(cr=cr, size=size)
    draw_icon_label(cr=cr, text=label, size=size)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
