"""Pure Cairo rendering for the Alarm applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.alarm.state import AlarmState, icon_label
from docking.applets.base import draw_icon_label


def render_icon(
    *,
    size: int,
    state: AlarmState,
    now,
) -> GdkPixbuf.Pixbuf | None:
    """Render an alarm clock icon with optional ringing state."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    ringing = state.ringing_index is not None
    _draw_clock(cr=cr, size=size, ringing=ringing)

    label = icon_label(state, now=now)
    if label:
        draw_icon_label(
            cr,
            label,
            size,
        )

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _draw_clock(cr: cairo.Context, *, size: int, ringing: bool) -> None:
    cx = size * 0.5
    cy = size * 0.54
    radius = size * 0.34

    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)

    # Bells.
    bell_color = (0.98, 0.72, 0.24, 1.0) if ringing else (0.88, 0.55, 0.20, 1.0)
    cr.set_source_rgba(*bell_color)
    cr.arc(size * 0.32, size * 0.22, size * 0.12, math.pi * 0.85, math.pi * 1.95)
    cr.arc(size * 0.68, size * 0.22, size * 0.12, math.pi * 1.05, math.pi * 2.15)
    cr.fill()

    # Feet.
    cr.set_line_width(max(2.0, size * 0.06))
    cr.move_to(size * 0.34, size * 0.84)
    cr.line_to(size * 0.25, size * 0.93)
    cr.move_to(size * 0.66, size * 0.84)
    cr.line_to(size * 0.75, size * 0.93)
    cr.stroke()

    # Body.
    cr.set_source_rgba(0.12, 0.15, 0.20, 1.0)
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.fill()
    cr.set_source_rgba(0.94, 0.96, 0.98, 1.0)
    cr.arc(cx, cy, radius * 0.86, 0, math.tau)
    cr.fill()

    # Tick marks.
    cr.set_source_rgba(0.18, 0.22, 0.28, 1.0)
    cr.set_line_width(max(1.0, size * 0.025))
    for index in range(12):
        angle = math.tau * index / 12.0 - math.pi / 2.0
        outer = radius * 0.72
        inner = radius * (0.58 if index % 3 == 0 else 0.65)
        cr.move_to(cx + math.cos(angle) * inner, cy + math.sin(angle) * inner)
        cr.line_to(cx + math.cos(angle) * outer, cy + math.sin(angle) * outer)
    cr.stroke()

    # Hands at a stable representative alarm time.
    cr.set_line_width(max(1.8, size * 0.045))
    cr.move_to(cx, cy)
    cr.line_to(cx, cy - radius * 0.48)
    cr.move_to(cx, cy)
    cr.line_to(cx + radius * 0.42, cy)
    cr.stroke()

    cr.arc(cx, cy, size * 0.035, 0, math.tau)
    cr.fill()

    if ringing:
        cr.set_source_rgba(0.95, 0.28, 0.24, 0.95)
        cr.set_line_width(max(1.4, size * 0.035))
        for side in (-1, 1):
            cr.arc(
                cx + side * size * 0.31,
                size * 0.27,
                size * 0.12,
                -math.pi * 0.35 if side > 0 else math.pi * 0.85,
                math.pi * 0.35 if side > 0 else math.pi * 1.15,
            )
            cr.stroke()
