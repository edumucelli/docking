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

"""Pure Cairo rendering for the Sunrise applet icon."""

from __future__ import annotations

import datetime as dt
import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label
from docking.applets.draw import rounded_rect
from docking.applets.sunrise.state import (
    DAY_MINUTES,
    SolarDay,
    SolarPhase,
    SolarSnapshot,
    icon_label,
)
from docking.core.math import clamp

RGBA = tuple[float, float, float, float]

_PHASE_COLORS: dict[SolarPhase, RGBA] = {
    SolarPhase.NIGHT: (0.04, 0.06, 0.16, 1.0),
    SolarPhase.ASTRONOMICAL: (0.12, 0.11, 0.34, 1.0),
    SolarPhase.NAUTICAL: (0.08, 0.26, 0.52, 1.0),
    SolarPhase.CIVIL: (0.92, 0.48, 0.18, 1.0),
    SolarPhase.DAYLIGHT: (1.0, 0.78, 0.20, 1.0),
}
_MARKER = (1.0, 1.0, 1.0, 0.95)
_SHADOW = (0.0, 0.0, 0.0, 0.35)


def render_icon(*, size: int, snapshot: SolarSnapshot) -> GdkPixbuf.Pixbuf | None:
    """Render the rich 24-hour solar dial."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    cx = size / 2.0
    cy = size / 2.0
    radius = size * 0.38
    ring_width = max(3.0, size * 0.15)

    _draw_background(cr=cr, size=size)
    _draw_phase_ring(
        cr=cr,
        day=snapshot.today,
        cx=cx,
        cy=cy,
        radius=radius,
        width=ring_width,
    )
    _draw_now_marker(
        cr=cr,
        now=snapshot.now,
        cx=cx,
        cy=cy,
        radius=radius,
        width=ring_width,
    )
    _draw_center_symbol(
        cr=cr,
        phase=snapshot.phase,
        cx=cx,
        cy=cy,
        size=size,
    )
    label = icon_label(snapshot)
    if label:
        draw_icon_label(
            cr=cr,
            text=label,
            size=size,
            max_width=size * 0.86,
            fill_rgba=(1, 1, 1, 1),
            outline_rgba=(0, 0, 0, 0.65),
        )

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _draw_background(*, cr: cairo.Context, size: int) -> None:
    margin = size * 0.06
    tile = size - (2 * margin)
    radius = tile * 0.10
    for i in range(10):
        t = (i + 1) / 10
        expand = size * 0.04 * t
        alpha = 0.14 * (1.0 - t)
        rounded_rect(
            cr=cr,
            x=margin - expand,
            y=margin - expand,
            width=tile + (2 * expand),
            height=tile + (2 * expand),
            radius=radius + expand,
        )
        cr.set_source_rgba(0.0, 0.0, 0.0, alpha)
        cr.fill()

    rounded_rect(
        cr=cr,
        x=margin,
        y=margin,
        width=tile,
        height=tile,
        radius=radius,
    )
    cr.clip()
    gradient = cairo.RadialGradient(
        size * 0.38,
        size * 0.30,
        size * 0.04,
        size / 2,
        size / 2,
        size * 0.72,
    )
    gradient.add_color_stop_rgba(0.0, 0.20, 0.27, 0.40, 1.0)
    gradient.add_color_stop_rgba(1.0, 0.02, 0.03, 0.09, 1.0)
    cr.rectangle(margin, margin, tile, tile)
    cr.set_source(gradient)
    cr.fill()


def _draw_phase_ring(
    *,
    cr: cairo.Context,
    day: SolarDay | None,
    cx: float,
    cy: float,
    radius: float,
    width: float,
) -> None:
    cr.set_line_width(width)
    if day is None:
        _stroke_full_ring(
            cr=cr, cx=cx, cy=cy, radius=radius, color=_PHASE_COLORS[SolarPhase.NIGHT]
        )
        return
    if day.polar_day:
        _stroke_full_ring(
            cr=cr,
            cx=cx,
            cy=cy,
            radius=radius,
            color=_PHASE_COLORS[SolarPhase.DAYLIGHT],
        )
        return
    if day.polar_night:
        _stroke_full_ring(
            cr=cr, cx=cx, cy=cy, radius=radius, color=_PHASE_COLORS[SolarPhase.NIGHT]
        )
        return

    _stroke_full_ring(
        cr=cr, cx=cx, cy=cy, radius=radius, color=_PHASE_COLORS[SolarPhase.NIGHT]
    )
    segments = (
        ("astronomical_dawn", "nautical_dawn", SolarPhase.ASTRONOMICAL),
        ("nautical_dawn", "civil_dawn", SolarPhase.NAUTICAL),
        ("civil_dawn", "sunrise", SolarPhase.CIVIL),
        ("sunrise", "sunset", SolarPhase.DAYLIGHT),
        ("sunset", "civil_dusk", SolarPhase.CIVIL),
        ("civil_dusk", "nautical_dusk", SolarPhase.NAUTICAL),
        ("nautical_dusk", "astronomical_dusk", SolarPhase.ASTRONOMICAL),
    )
    for start_key, end_key, phase in segments:
        start = _minute_of_day(day.event(start_key))
        end = _minute_of_day(day.event(end_key))
        if start is None or end is None:
            continue
        _stroke_segment(
            cr=cr,
            cx=cx,
            cy=cy,
            radius=radius,
            start_minute=start,
            end_minute=end,
            color=_PHASE_COLORS[phase],
        )


def _draw_now_marker(
    *,
    cr: cairo.Context,
    now: dt.datetime,
    cx: float,
    cy: float,
    radius: float,
    width: float,
) -> None:
    minute = now.hour * 60 + now.minute + now.second / 60.0
    angle = _minute_angle(minute)
    outer = radius + width * 0.62
    inner = radius - width * 0.62
    cr.set_line_width(max(1.5, width * 0.16))
    cr.set_source_rgba(*_SHADOW)
    cr.move_to(
        cx + math.cos(angle) * (inner - 1.0), cy + math.sin(angle) * (inner - 1.0)
    )
    cr.line_to(
        cx + math.cos(angle) * (outer + 1.0), cy + math.sin(angle) * (outer + 1.0)
    )
    cr.stroke()
    cr.set_source_rgba(*_MARKER)
    cr.move_to(cx + math.cos(angle) * inner, cy + math.sin(angle) * inner)
    cr.line_to(cx + math.cos(angle) * outer, cy + math.sin(angle) * outer)
    cr.stroke()


def _draw_center_symbol(
    *,
    cr: cairo.Context,
    phase: SolarPhase,
    cx: float,
    cy: float,
    size: int,
) -> None:
    if phase == SolarPhase.DAYLIGHT:
        _draw_sun(cr=cr, cx=cx, cy=cy, size=size)
    elif phase == SolarPhase.NIGHT:
        _draw_moon(cr=cr, cx=cx, cy=cy, size=size)
    else:
        _draw_horizon(cr=cr, cx=cx, cy=cy, size=size, phase=phase)


def _draw_sun(*, cr: cairo.Context, cx: float, cy: float, size: int) -> None:
    radius = size * 0.13
    cr.set_line_width(max(1.2, size * 0.035))
    cr.set_source_rgba(1.0, 0.82, 0.22, 0.95)
    for i in range(10):
        angle = i * math.tau / 10
        inner = radius * 1.45
        outer = radius * 2.0
        cr.move_to(cx + math.cos(angle) * inner, cy + math.sin(angle) * inner)
        cr.line_to(cx + math.cos(angle) * outer, cy + math.sin(angle) * outer)
        cr.stroke()
    sun = cairo.RadialGradient(
        cx - radius * 0.35, cy - radius * 0.35, 1, cx, cy, radius
    )
    sun.add_color_stop_rgba(0, 1.0, 0.96, 0.54, 1.0)
    sun.add_color_stop_rgba(1, 0.96, 0.48, 0.08, 1.0)
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.set_source(sun)
    cr.fill()


def _draw_moon(*, cr: cairo.Context, cx: float, cy: float, size: int) -> None:
    radius = size * 0.16
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.set_source_rgba(0.92, 0.90, 0.78, 0.95)
    cr.fill()
    cr.arc(cx + radius * 0.42, cy - radius * 0.10, radius * 0.92, 0, math.tau)
    cr.set_source_rgba(0.05, 0.07, 0.16, 1.0)
    cr.fill()
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.85)
    for sx, sy, sr in (
        (cx - radius * 1.5, cy - radius * 1.0, radius * 0.12),
        (cx + radius * 1.2, cy + radius * 0.9, radius * 0.10),
    ):
        cr.arc(sx, sy, sr, 0, math.tau)
        cr.fill()


def _draw_horizon(
    *,
    cr: cairo.Context,
    cx: float,
    cy: float,
    size: int,
    phase: SolarPhase,
) -> None:
    width = size * 0.42
    y = cy + size * 0.05
    sun_y = y if phase == SolarPhase.CIVIL else y + size * 0.05
    cr.set_line_width(max(1.5, size * 0.035))
    cr.set_source_rgba(1.0, 0.72, 0.24, 0.95)
    cr.arc(cx, sun_y, size * 0.12, math.pi, math.tau)
    cr.stroke()
    cr.set_source_rgba(0.42, 0.76, 1.0, 0.85)
    cr.move_to(cx - width / 2, y)
    cr.line_to(cx + width / 2, y)
    cr.stroke()
    cr.set_source_rgba(0.18, 0.42, 0.72, 0.75)
    cr.move_to(cx - width * 0.42, y + size * 0.08)
    cr.line_to(cx + width * 0.42, y + size * 0.08)
    cr.stroke()


def _stroke_full_ring(
    *,
    cr: cairo.Context,
    cx: float,
    cy: float,
    radius: float,
    color: RGBA,
) -> None:
    cr.set_source_rgba(*color)
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.stroke()


def _stroke_segment(
    *,
    cr: cairo.Context,
    cx: float,
    cy: float,
    radius: float,
    start_minute: float,
    end_minute: float,
    color: RGBA,
) -> None:
    start = clamp(start_minute, 0.0, float(DAY_MINUTES))
    end = clamp(end_minute, 0.0, float(DAY_MINUTES))
    if end <= start:
        return
    cr.set_source_rgba(*color)
    cr.arc(cx, cy, radius, _minute_angle(start), _minute_angle(end))
    cr.stroke()


def _minute_angle(minute: float) -> float:
    return -math.pi / 2.0 + (minute / DAY_MINUTES) * math.tau


def _minute_of_day(value: dt.datetime | None) -> float | None:
    if value is None:
        return None
    return value.hour * 60.0 + value.minute + value.second / 60.0
