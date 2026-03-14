"""Pure Cairo renderer for Desktop applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.draw import rounded_rect

_FRAME = (0.10, 0.24, 0.48)
_SCREEN = (0.60, 0.84, 0.74)
_BEZEL = (0.92, 0.60, 0.60)


def _bottom_rounded_rect(
    *,
    cr: cairo.Context,
    x: float,
    y: float,
    w: float,
    h: float,
    r: float,
) -> None:
    r = max(0.0, min(r, min(w, h) / 2))
    cr.new_path()
    cr.move_to(x, y)
    cr.line_to(x + w, y)
    cr.line_to(x + w, y + h - r)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.line_to(x + r, y + h)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.close_path()


def create_icon(*, size: int) -> GdkPixbuf.Pixbuf | None:
    """Render a stylized monitor icon (no circular background)."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()

    # Monitor body
    mx = size * 0.12
    my = size * 0.10
    mw = size * 0.76
    mh = size * 0.64
    rounded_rect(cr=cr, x=mx, y=my, width=mw, height=mh, radius=size * 0.11)
    cr.set_source_rgb(*_FRAME)
    cr.fill()

    # Inner display area
    pad = size * 0.035
    ix = mx + pad
    iy = my + pad
    iw = mw - 2 * pad
    ih = mh - 2 * pad
    rounded_rect(cr=cr, x=ix, y=iy, width=iw, height=ih, radius=size * 0.07)
    cr.set_source_rgb(*_SCREEN)
    cr.fill()

    # Bottom bezel strip
    bezel_h = ih * 0.22
    by = iy + ih - bezel_h
    _bottom_rounded_rect(cr=cr, x=ix, y=by, w=iw, h=bezel_h, r=size * 0.06)
    cr.set_source_rgb(*_BEZEL)
    cr.fill()

    # Power/status dot
    cr.set_source_rgb(*_FRAME)
    cr.arc(mx + mw * 0.50, by + bezel_h * 0.55, size * 0.020, 0, math.tau)
    cr.fill()

    # Stand legs + foot
    y1 = my + mh
    y2 = size * 0.84
    cr.set_source_rgb(*_FRAME)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(max(1.4, size * 0.065))
    cr.move_to(mx + mw * 0.44, y1 + size * 0.01)
    cr.line_to(mx + mw * 0.41, y2)
    cr.stroke()
    cr.move_to(mx + mw * 0.56, y1 + size * 0.01)
    cr.line_to(mx + mw * 0.59, y2)
    cr.stroke()

    cr.set_line_width(max(1.6, size * 0.075))
    cr.move_to(mx + mw * 0.30, y2 + size * 0.01)
    cr.line_to(mx + mw * 0.70, y2 + size * 0.01)
    cr.stroke()

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
