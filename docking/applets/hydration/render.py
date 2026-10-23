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

"""Pure Cairo rendering for hydration applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label
from docking.applets.hydration.state import (
    HydrationState,
    format_remaining,
    mouth_curvature,
    water_color,
)


def _draw_drop_path(cr: cairo.Context, size: int) -> None:
    """Draw a teardrop/water drop path centered in size x size."""
    cx = size / 2
    tip_y = size * 0.08
    bot_y = size * 0.92
    # Widest point of the drop
    bulge_y = size * 0.64
    bulge_w = size * 0.38

    cr.new_path()
    cr.move_to(cx, tip_y)
    # Left side: tip down to bulge, curving outward
    cr.curve_to(
        cx - bulge_w * 0.3,
        size * 0.30,
        cx - bulge_w,
        bulge_y - size * 0.15,
        cx - bulge_w,
        bulge_y,
    )
    # Bottom: two curves through a single low point for a rounder belly.
    cr.curve_to(
        cx - bulge_w * 1.05,
        bulge_y + size * 0.18,
        cx - bulge_w * 0.45,
        bot_y,
        cx,
        bot_y,
    )
    cr.curve_to(
        cx + bulge_w * 0.45,
        bot_y,
        cx + bulge_w * 1.05,
        bulge_y + size * 0.18,
        cx + bulge_w,
        bulge_y,
    )
    # Right side: bulge back up to tip
    cr.curve_to(
        cx + bulge_w,
        bulge_y - size * 0.15,
        cx + bulge_w * 0.3,
        size * 0.30,
        cx,
        tip_y,
    )
    cr.close_path()


def _render_drop(cr: cairo.Context, size: int, fill: float) -> None:
    """Render a water drop with fill level."""
    red, green, blue = water_color()

    # Solid dark background so the drop shape is always visible
    _draw_drop_path(cr=cr, size=size)
    cr.set_source_rgb(0.12, 0.12, 0.18)
    cr.fill()

    # Water fill: clip to drop shape, draw rect from bottom.
    # Drop spans ~0.08 (tip) to ~0.92 (bottom). Map fill 1.0->tip, 0.0->bottom.
    if fill > 0:
        cr.save()
        _draw_drop_path(cr=cr, size=size)
        cr.clip()
        drop_top = size * 0.08
        drop_bot = size * 0.92
        fill_top = drop_bot - fill * (drop_bot - drop_top)
        cr.rectangle(0, fill_top, size, size - fill_top)
        cr.set_source_rgb(red, green, blue)
        cr.fill()
        cr.restore()

    # Thick white outline so the shape pops on any background
    _draw_drop_path(cr=cr, size=size)
    cr.set_source_rgba(1, 1, 1, 0.9)
    cr.set_line_width(max(1.3, size * 0.035))
    cr.stroke()

    # Small highlight on upper-left
    cr.save()
    highlight_x = size * 0.38
    highlight_y = size * 0.45
    cr.translate(highlight_x, highlight_y)
    cr.scale(size * 0.08, size * 0.12)
    cr.arc(0, 0, 1.0, 0, math.tau)
    cr.restore()
    cr.set_source_rgba(1, 1, 1, 0.3 * max(0.0, fill))
    cr.fill()

    # Face styled like Pomodoro (dark eyes + arc mouth). Mouth transitions
    # from smile -> neutral -> frown as water drains.
    cr.save()
    _draw_drop_path(cr=cr, size=size)
    cr.clip()

    cx = size / 2
    cy = size * 0.54
    eye_radius = size * 0.04
    eye_y = cy - size * 0.05
    eye_dx = size * 0.11

    # Eyes
    cr.set_source_rgba(0.12, 0.12, 0.16, 0.95)
    cr.arc(cx - eye_dx, eye_y, eye_radius, 0, math.tau)
    cr.fill()
    cr.arc(cx + eye_dx, eye_y, eye_radius, 0, math.tau)
    cr.fill()

    # Mouth (Pomodoro-like arc; flips as fill decreases)
    mouth_y = cy + size * 0.04
    mood = mouth_curvature(fill=fill)
    strength = abs(mood)

    # Near neutral, draw a short flat line.
    if strength < 0.08:
        half_width = size * 0.09
        cr.set_source_rgba(0.12, 0.12, 0.16, 0.95)
        cr.set_line_width(max(1.0, size * 0.03))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.move_to(cx - half_width, mouth_y)
        cr.line_to(cx + half_width, mouth_y)
        cr.stroke()
        cr.restore()
        return

    smile_radius = size * (0.04 + 0.06 * strength)
    cr.set_source_rgba(0.12, 0.12, 0.16, 0.95)
    cr.set_line_width(max(1.0, size * 0.03))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    if mood >= 0:
        # Smile arc (happy/full).
        cr.arc(cx, mouth_y, smile_radius, 0.2, math.pi - 0.2)
    else:
        # Frown arc (sad/empty).
        cr.arc(
            cx,
            mouth_y + size * 0.03,
            smile_radius,
            math.pi + 0.2,
            math.tau - 0.2,
        )
    cr.stroke()
    cr.restore()


def render_icon(size: int, state: HydrationState) -> GdkPixbuf.Pixbuf | None:
    """Render hydration icon for current state."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    _render_drop(cr=cr, size=size, fill=state.fill)
    if state.show_timer and state.fill > 0:
        text = format_remaining(fill=state.fill, interval_min=state.interval_min)
        draw_icon_label(cr=cr, text=text, size=size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
