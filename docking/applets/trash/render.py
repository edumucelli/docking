"""Pure Cairo renderer for Trash applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402


def trash_icon_name(*, item_count: int) -> str:
    return "user-trash-full" if item_count > 0 else "user-trash"


def trash_tooltip(*, item_count: int) -> str:
    if item_count == 0:
        return "No items in Trash"
    if item_count == 1:
        return "1 item in Trash"
    return f"{item_count} items in Trash"


def _rounded_rect(
    *,
    cr: cairo.Context,
    x: float,
    y: float,
    w: float,
    h: float,
    r: float,
) -> None:
    r = max(0.0, min(r, min(w, h) / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _draw_trash_can(*, cr: cairo.Context, size: int, item_count: int) -> None:
    """Draw stylized trash-can icon matching Dock identity."""
    # Slightly darker when non-empty so users can still perceive state.
    if item_count > 0:
        fill = (0.03, 0.62, 0.45)
        lid = (0.02, 0.56, 0.41)
    else:
        fill = (0.05, 0.66, 0.49)
        lid = (0.04, 0.58, 0.43)

    # Handle
    hx = size * 0.31
    hy = size * 0.07
    hw = size * 0.38
    hh = size * 0.12
    _rounded_rect(cr=cr, x=hx, y=hy, w=hw, h=hh, r=size * 0.045)
    cr.set_source_rgb(*lid)
    cr.set_line_width(max(1.4, size * 0.06))
    cr.stroke()

    # Lid
    lx = size * 0.06
    ly = size * 0.20
    lw = size * 0.88
    lh = size * 0.17
    _rounded_rect(cr=cr, x=lx, y=ly, w=lw, h=lh, r=size * 0.12)
    cr.set_source_rgb(*lid)
    cr.fill()

    # Body (slight trapezoid taper)
    top_y = size * 0.34
    bot_y = size * 0.92
    top_l = size * 0.10
    top_r = size * 0.90
    bot_l = size * 0.22
    bot_r = size * 0.78
    cr.new_path()
    cr.move_to(top_l, top_y)
    cr.line_to(top_r, top_y)
    cr.line_to(bot_r, bot_y)
    cr.line_to(bot_l, bot_y)
    cr.close_path()
    cr.set_source_rgb(*fill)
    cr.fill()

    # Inner slits
    cr.set_source_rgb(0.92, 0.95, 0.97)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(max(1.5, size * 0.06))

    # Left
    cr.move_to(size * 0.36, size * 0.47)
    cr.line_to(size * 0.43, size * 0.80)
    cr.stroke()

    # Center
    cr.move_to(size * 0.50, size * 0.47)
    cr.line_to(size * 0.50, size * 0.80)
    cr.stroke()

    # Right
    cr.move_to(size * 0.64, size * 0.47)
    cr.line_to(size * 0.57, size * 0.80)
    cr.stroke()


def create_trash_icon(*, size: int, item_count: int) -> GdkPixbuf.Pixbuf | None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()
    _draw_trash_can(cr=cr, size=size, item_count=item_count)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
