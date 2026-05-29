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

"""Pure Cairo rendering helpers for Battery applet."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label
from docking.applets.battery.state import (
    OVERLAY_NONE,
    OVERLAY_PERCENT,
    OVERLAY_POWER,
    BatteryState,
    format_power,
)
from docking.applets.draw import rounded_rect
from docking.core.math import clamp

_GREEN = (0.00, 0.62, 0.32)
_GREEN_DARK = (0.00, 0.55, 0.28)
_BG = (1.0, 1.0, 1.0)
_DISABLED = (0.72, 0.72, 0.72)


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

    rounded_rect(cr=cr, x=cap_x, y=cap_y, width=cap_w, height=cap_h, radius=size * 0.02)
    cr.set_source_rgb(*_BG)
    cr.fill()

    rounded_rect(
        cr=cr,
        x=x,
        y=y,
        width=w,
        height=h,
        radius=r,
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
    rounded_rect(cr=cr, x=cap_x, y=cap_y, width=cap_w, height=cap_h, radius=size * 0.02)
    cr.set_source_rgb(*_BG)
    cr.fill()

    # Outer frame
    rounded_rect(cr=cr, x=x, y=y, width=w, height=h, radius=r)
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
    ratio = clamp(capacity / 100.0, 0.0, 1.0)

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


def _overlay_label(*, state: BatteryState, overlay: str) -> str | None:
    """Bottom-center overlay text for the selected mode, or None."""
    if overlay == OVERLAY_PERCENT:
        return f"{state.capacity}%"
    if overlay == OVERLAY_POWER and state.power_watts is not None:
        return format_power(state.power_watts, compact=True)
    return None


def render_icon(
    size: int,
    state: BatteryState | None,
    overlay: str = OVERLAY_NONE,
) -> GdkPixbuf.Pixbuf | None:
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
        label = _overlay_label(state=state, overlay=overlay)
        if label is not None:
            draw_icon_label(cr=cr, text=label, size=size)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
