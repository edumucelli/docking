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

"""Pure Cairo rendering for Pomodoro icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label
from docking.applets.pomodoro.state import PomodoroState, State, format_time

# RGB tuples per state (tomato body color)
STATE_COLORS: dict[State, tuple[float, float, float]] = {
    State.IDLE: (0.85, 0.16, 0.12),
    State.WORK: (0.85, 0.16, 0.12),
    State.BREAK: (0.25, 0.72, 0.35),
    State.LONG_BREAK: (0.30, 0.55, 0.85),
    State.PAUSED: (0.85, 0.16, 0.12),
}


def _draw_tomato(
    cr: cairo.Context,
    size: int,
    red: float,
    green: float,
    blue: float,
    alpha: float,
) -> None:
    """Draw a flat tomato: red ellipse body + green stem/leaf."""
    cx = size / 2
    # Shift body down slightly to leave room for stem
    cy = size * 0.55
    radius_x = size * 0.40  # horizontal radius
    radius_y = size * 0.36  # vertical radius (squatter)

    # Body
    cr.save()
    cr.translate(cx, cy)
    cr.scale(radius_x, radius_y)
    cr.arc(0, 0, 1.0, 0, math.tau)
    cr.restore()
    cr.set_source_rgba(red, green, blue, alpha)
    cr.fill()

    # Subtle highlight (lighter ellipse in upper-left)
    cr.save()
    cr.translate(cx - radius_x * 0.25, cy - radius_y * 0.30)
    cr.scale(radius_x * 0.45, radius_y * 0.35)
    cr.arc(0, 0, 1.0, 0, math.tau)
    cr.restore()
    cr.set_source_rgba(1, 1, 1, 0.18 * alpha)
    cr.fill()

    # Stem (small green rectangle)
    stem_w = size * 0.06
    stem_h = size * 0.12
    stem_x = cx - stem_w / 2
    stem_y = cy - radius_y - stem_h * 0.5
    cr.rectangle(stem_x, stem_y, stem_w, stem_h)
    cr.set_source_rgba(0.25, 0.55, 0.20, alpha)
    cr.fill()

    # Leaf (small green ellipse tilted right)
    leaf_cx = cx + size * 0.06
    leaf_cy = cy - radius_y - stem_h * 0.15
    cr.save()
    cr.translate(leaf_cx, leaf_cy)
    cr.rotate(0.5)  # slight tilt
    cr.scale(size * 0.10, size * 0.05)
    cr.arc(0, 0, 1.0, 0, math.tau)
    cr.restore()
    cr.set_source_rgba(0.30, 0.65, 0.25, alpha)
    cr.fill()


def _draw_face(cr: cairo.Context, size: int) -> None:
    """Draw a cute face on the tomato (IDLE state only)."""
    cx = size / 2
    cy = size * 0.52
    eye_r = size * 0.04
    eye_y = cy - size * 0.04
    eye_offset = size * 0.12

    # Eyes
    cr.set_source_rgba(0.15, 0.15, 0.15, 1)
    cr.arc(cx - eye_offset, eye_y, eye_r, 0, math.tau)
    cr.fill()
    cr.arc(cx + eye_offset, eye_y, eye_r, 0, math.tau)
    cr.fill()

    # Smile (arc)
    smile_radius = size * 0.10
    smile_y = cy + size * 0.04
    cr.set_line_width(max(1.0, size * 0.03))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.arc(cx, smile_y, smile_radius, 0.2, math.pi - 0.2)
    cr.stroke()


def render_icon(size: int, state: PomodoroState) -> GdkPixbuf.Pixbuf | None:
    """Render icon for current Pomodoro state."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    display_state = state.paused_from if state.phase == State.PAUSED else state.phase
    red, green, blue = STATE_COLORS.get(display_state, (0.85, 0.16, 0.12))
    alpha = 0.5 if state.phase == State.PAUSED else 1.0

    _draw_tomato(cr=cr, size=size, red=red, green=green, blue=blue, alpha=alpha)

    _draw_face(cr=cr, size=size)
    if state.phase != State.IDLE and state.show_timer:
        draw_icon_label(
            cr=cr,
            text=format_time(seconds=state.remaining),
            size=size,
        )

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
