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

"""Pure Cairo rendering for the Caffeine icon.

A flat coffee mug. When active it is a warm, filled cup with rising steam; when
off it is a muted, empty cup. A countdown label is drawn at the bottom while a
timed session is running.
"""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label
from docking.applets.caffeine.state import CaffeineState, format_remaining, has_timer

# RGB palettes per active/inactive state. The off cup is greyed and dimmed (via
# _ALPHA_OFF) with no steam, so the active/inactive difference reads clearly.
_CERAMIC_ON = (0.93, 0.93, 0.96)
_CERAMIC_OFF = (0.55, 0.57, 0.61)
_COFFEE_ON = (0.36, 0.20, 0.09)
_COFFEE_OFF = (0.34, 0.35, 0.38)
_OUTLINE_ON = (0.20, 0.20, 0.24)
_OUTLINE_OFF = (0.36, 0.37, 0.40)

_ALPHA_OFF = 0.6


def _rounded_rect(
    cr: cairo.Context, x: float, y: float, w: float, h: float, r: float
) -> None:
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _draw_steam(cr: cairo.Context, size: int, cx: float, top: float) -> None:
    """Draw two rising steam wisps above the cup."""
    cr.set_source_rgba(1, 1, 1, 0.55)
    cr.set_line_width(max(1.0, size * 0.035))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    amp = size * 0.05
    height = size * 0.18
    for offset in (-size * 0.10, size * 0.10):
        x = cx + offset
        cr.move_to(x, top)
        steps = 8
        for i in range(1, steps + 1):
            t = i / steps
            sy = top - height * t
            sx = x + amp * math.sin(t * math.tau)
            cr.line_to(sx, sy)
        cr.stroke()


def _draw_mug(cr: cairo.Context, size: int, active: bool) -> None:
    ceramic = _CERAMIC_ON if active else _CERAMIC_OFF
    coffee = _COFFEE_ON if active else _COFFEE_OFF
    outline = _OUTLINE_ON if active else _OUTLINE_OFF
    alpha = 1.0 if active else _ALPHA_OFF

    # Body + handle are sized and offset so the mug fills the canvas like the
    # other applet icons (centered around 0.5 horizontally).
    body_x = size * 0.19
    body_y = size * 0.34
    body_w = size * 0.48
    body_h = size * 0.46
    radius = size * 0.08
    line_w = max(1.0, size * 0.045)

    # Handle (drawn first so the body overlaps its inner edge).
    handle_cx = body_x + body_w + size * 0.005
    handle_cy = body_y + body_h * 0.42
    cr.save()
    cr.set_line_width(line_w * 1.4)
    cr.arc(handle_cx, handle_cy, size * 0.135, -math.pi / 2.2, math.pi / 2.2)
    cr.set_source_rgba(*outline, alpha)
    cr.stroke()
    cr.restore()

    # Body.
    _rounded_rect(cr, body_x, body_y, body_w, body_h, radius)
    cr.set_source_rgba(*ceramic, alpha)
    cr.fill_preserve()
    cr.set_line_width(line_w)
    cr.set_source_rgba(*outline, alpha)
    cr.stroke()

    # Coffee surface: a filled ellipse inset near the cup rim.
    surf_cx = body_x + body_w / 2
    surf_cy = body_y + body_h * 0.18
    surf_rx = body_w * 0.36
    surf_ry = body_h * 0.12
    cr.save()
    cr.translate(surf_cx, surf_cy)
    cr.scale(surf_rx, surf_ry)
    cr.arc(0, 0, 1.0, 0, math.tau)
    cr.restore()
    cr.set_source_rgba(*coffee, alpha)
    cr.fill()

    if active:
        _draw_steam(cr=cr, size=size, cx=surf_cx, top=body_y - size * 0.02)


def render_icon(*, size: int, state: CaffeineState) -> GdkPixbuf.Pixbuf | None:
    """Render the Caffeine icon for the current state."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    _draw_mug(cr=cr, size=size, active=state.active)

    if has_timer(state=state):
        draw_icon_label(
            cr=cr,
            text=format_remaining(seconds=state.remaining),
            size=size,
        )

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
