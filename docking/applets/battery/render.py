"""Pure Cairo rendering helpers for Battery applet."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

from docking.applets.battery.state import BatteryState

_GREEN = (0.00, 0.62, 0.32)
_GREEN_DARK = (0.00, 0.55, 0.28)
_BG = (1.0, 1.0, 1.0)
_DISABLED = (0.72, 0.72, 0.72)


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


def _draw_no_battery(*, cr: cairo.Context, size: int) -> None:
    """Draw disabled battery glyph when no battery is present."""
    x = size * 0.24
    y = size * 0.14
    w = size * 0.52
    h = size * 0.74
    r = size * 0.08
    cap_w = w * 0.22
    cap_h = size * 0.075
    cap_x = x + (w - cap_w) / 2
    cap_y = y - cap_h - size * 0.015

    _rounded_rect(cr=cr, x=cap_x, y=cap_y, w=cap_w, h=cap_h, r=size * 0.02)
    cr.set_source_rgb(*_BG)
    cr.fill()

    _rounded_rect(
        cr=cr,
        x=x,
        y=y,
        w=w,
        h=h,
        r=r,
    )
    cr.set_source_rgb(*_DISABLED)
    cr.set_line_width(max(1.6, size * 0.07))
    cr.stroke()
    cr.move_to(x + w * 0.15, y + h * 0.85)
    cr.line_to(x + w * 0.85, y + h * 0.15)
    cr.stroke()


def _draw_battery(
    *,
    cr: cairo.Context,
    size: int,
    capacity: int,
    charging: bool,
) -> None:
    """Draw battery body with green level fill and optional charging marker."""
    x = size * 0.24
    y = size * 0.14
    w = size * 0.52
    h = size * 0.74
    r = size * 0.08

    # Battery cap
    cap_w = w * 0.22
    cap_h = size * 0.075
    cap_x = x + (w - cap_w) / 2
    cap_y = y - cap_h - size * 0.015
    _rounded_rect(cr=cr, x=cap_x, y=cap_y, w=cap_w, h=cap_h, r=size * 0.02)
    cr.set_source_rgb(*_BG)
    cr.fill()

    # Outer frame
    _rounded_rect(cr=cr, x=x, y=y, w=w, h=h, r=r)
    cr.set_source_rgb(*_GREEN)
    cr.set_line_width(max(1.8, size * 0.08))
    cr.stroke_preserve()
    cr.set_source_rgb(*_BG)
    cr.fill()

    # Inner fill
    inner_pad = size * 0.08
    ix = x + inner_pad
    iy = y + inner_pad
    iw = w - inner_pad * 2
    ih = h - inner_pad * 2
    ratio = max(0.0, min(1.0, capacity / 100.0))

    if ratio > 0:
        fill_h = ih * ratio
        fill_y = iy + ih - fill_h
        cr.rectangle(ix, fill_y, iw, fill_h)
        cr.set_source_rgb(*_GREEN_DARK if ratio < 0.20 else _GREEN)
        cr.fill()

    if charging:
        cx = x + w * 0.50
        cy = y + h * 0.50
        bolt = size * 0.09
        cr.move_to(cx + bolt * 0.15, cy - bolt * 1.35)
        cr.line_to(cx - bolt * 0.45, cy - bolt * 0.25)
        cr.line_to(cx, cy - bolt * 0.25)
        cr.line_to(cx - bolt * 0.20, cy + bolt * 1.20)
        cr.line_to(cx + bolt * 0.60, cy + bolt * 0.10)
        cr.line_to(cx + bolt * 0.05, cy + bolt * 0.10)
        cr.close_path()
        cr.set_source_rgb(*_BG)
        cr.fill()


def render_icon(size: int, state: BatteryState | None) -> GdkPixbuf.Pixbuf | None:
    """Render standardized battery icon independent from system theme."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()

    if state is None:
        _draw_no_battery(cr=cr, size=size)
    else:
        _draw_battery(
            cr=cr,
            size=size,
            capacity=state.capacity,
            charging=state.icon_name.endswith("-charging"),
        )

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
