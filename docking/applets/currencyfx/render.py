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

"""Pure Cairo rendering for the Currency FX applet icon.

The renderer receives already-prepared state.  It does not know whether points
came from daily history or the local day cache; it only draws the active
pair and the ordered rates.  Keeping it data-source agnostic lets tests render
synthetic snapshots without GTK timers or network fetches.

Icon layout:

* base currency code is drawn near the top in a smaller bold label.
* the sparkline occupies the middle of the icon.
* quote currency code uses the shared large applet label at the bottom.
* a flat warning/loading line is drawn when there are no chart points.
* the latest sparkline point can receive a pulsing halo from the applet timer.
"""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label
from docking.applets.currencyfx.state import FxPoint, FxSnapshot, percent_change
from docking.applets.draw import rounded_rect
from docking.core.math import clamp

_BG_TOP = (0.08, 0.11, 0.15)
_BG_BOTTOM = (0.12, 0.16, 0.22)
_GRID = (1.0, 1.0, 1.0, 0.14)
_LINE_UP = (0.28, 0.78, 0.48)
_LINE_DOWN = (0.95, 0.36, 0.30)
_LINE_FLAT = (0.45, 0.62, 0.86)
_TEXT = (1.0, 1.0, 1.0, 0.90)
_WARN = (0.95, 0.60, 0.22)


def render_icon(
    *,
    size: int,
    snapshot: FxSnapshot | None,
    base: str,
    quote: str,
    fetch_failed: bool = False,
    pulse_phase: float | None = None,
) -> GdkPixbuf.Pixbuf | None:
    """Render a live FX sparkline icon.

    ``snapshot`` supplies the chart points and current rate context.  ``base``
    and ``quote`` are passed separately so the icon can still label the pair
    while data is loading or unavailable.  ``pulse_phase`` animates the latest
    point only when the applet has a live timer.
    """
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    _draw_background(cr=cr, size=size)
    _draw_grid(cr=cr, size=size)

    points = snapshot.points if snapshot else ()
    if points:
        _draw_sparkline(
            cr=cr,
            size=size,
            points=points,
            pulse_phase=pulse_phase,
        )
        if fetch_failed:
            _draw_warning_badge(cr=cr, size=size)
    else:
        _draw_empty_state(cr=cr, size=size, failed=fetch_failed)

    _draw_currency_label(cr=cr, size=size, text=base.upper(), y=size * 0.19)
    _draw_target_label(cr=cr, size=size, text=quote.upper())
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _draw_background(*, cr: cairo.Context, size: int) -> None:
    """Draw the rounded icon background and subtle border."""
    pad = size * 0.06
    radius = size * 0.15
    grad = cairo.LinearGradient(0, pad, 0, size - pad)
    grad.add_color_stop_rgb(0, *_BG_TOP)
    grad.add_color_stop_rgb(1, *_BG_BOTTOM)
    rounded_rect(
        cr=cr,
        x=pad,
        y=pad,
        width=size - pad * 2,
        height=size - pad * 2,
        radius=radius,
    )
    cr.set_source(grad)
    cr.fill_preserve()
    cr.set_line_width(max(1.0, size * 0.025))
    cr.set_source_rgba(1, 1, 1, 0.20)
    cr.stroke()


def _draw_grid(*, cr: cairo.Context, size: int) -> None:
    """Draw low-contrast horizontal guide lines behind the sparkline."""
    left = size * 0.18
    right = size * 0.86
    top = size * 0.25
    bottom = size * 0.72
    cr.set_line_width(max(0.7, size * 0.015))
    cr.set_source_rgba(*_GRID)
    for frac in (0.0, 0.5, 1.0):
        y = top + (bottom - top) * frac
        cr.move_to(left, y)
        cr.line_to(right, y)
    cr.stroke()


def _draw_sparkline(
    *,
    cr: cairo.Context,
    size: int,
    points: tuple[FxPoint, ...],
    pulse_phase: float | None = None,
) -> None:
    """Draw the rate sparkline scaled to the available middle area.

    The chart uses the full local min/max range of visible points.  If every
    point has the same value, a tiny synthetic span is added so the line still
    renders in the middle instead of collapsing to an undefined scale.
    """
    values = [point.rate for point in points if point.rate > 0]
    if not values:
        return

    left = size * 0.16
    right = size * 0.86
    top = size * 0.22
    bottom = size * 0.70
    min_rate = min(values)
    max_rate = max(values)
    span = max_rate - min_rate
    if span <= 0:
        span = max_rate * 0.01 if max_rate else 1.0
        min_rate -= span / 2
        max_rate += span / 2
        span = max_rate - min_rate

    coords: list[tuple[float, float]] = []
    count = len(values)
    for index, rate in enumerate(values):
        x_frac = index / max(1, count - 1)
        y_frac = (rate - min_rate) / span
        coords.append(
            (
                left + (right - left) * x_frac,
                bottom - (bottom - top) * y_frac,
            )
        )

    # Color follows the total interval change, not the last segment, because
    # the icon needs to communicate the selected window at dock size.
    change = percent_change(points)
    color = _LINE_FLAT
    if change is not None and change > 0.01:
        color = _LINE_UP
    elif change is not None and change < -0.01:
        color = _LINE_DOWN

    cr.save()
    cr.set_line_width(max(2.0, size * 0.06))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_source_rgba(color[0], color[1], color[2], 0.24)
    _trace_line(cr=cr, coords=coords)
    cr.stroke()
    cr.set_line_width(max(1.2, size * 0.035))
    cr.set_source_rgb(*color)
    _trace_line(cr=cr, coords=coords)
    cr.stroke()
    cr.restore()

    last_x, last_y = coords[-1]
    dot_radius = max(1.7, size * 0.055)
    if pulse_phase is not None:
        phase = clamp(pulse_phase, 0.0, 1.0)
        pulse_radius = dot_radius * (1.18 + 0.82 * phase)
        pulse_alpha = 0.46 * (1.0 - phase)
        if pulse_alpha > 0.02:
            cr.arc(last_x, last_y, pulse_radius, 0, math.tau)
            cr.set_source_rgba(color[0], color[1], color[2], pulse_alpha)
            cr.fill()
        dot_radius *= 0.94 + 0.10 * math.sin(math.tau * phase)

    cr.arc(last_x, last_y, dot_radius, 0, math.tau)
    cr.set_source_rgb(*color)
    cr.fill_preserve()
    cr.set_line_width(max(0.8, size * 0.018))
    cr.set_source_rgba(1, 1, 1, 0.85)
    cr.stroke()


def _trace_line(*, cr: cairo.Context, coords: list[tuple[float, float]]) -> None:
    """Trace the coordinate list without choosing stroke style."""
    if not coords:
        return
    cr.move_to(*coords[0])
    for x, y in coords[1:]:
        cr.line_to(x, y)


def _draw_currency_label(
    *,
    cr: cairo.Context,
    size: int,
    text: str,
    y: float,
) -> None:
    """Draw a centered currency code at a fixed vertical position."""
    cr.save()
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(max(6.0, size * 0.17))
    ext = cr.text_extents(text)
    max_width = size * 0.78
    if ext.width > max_width and ext.width > 0:
        cr.set_font_size(max(5.0, size * 0.17 * max_width / ext.width))
        ext = cr.text_extents(text)
    x = (size - ext.width) / 2 - ext.x_bearing
    cr.set_source_rgba(0, 0, 0, 0.50)
    cr.move_to(x + 1, y + 1)
    cr.show_text(text)
    cr.set_source_rgba(*_TEXT)
    cr.move_to(x, y)
    cr.show_text(text)
    cr.restore()


def _draw_target_label(*, cr: cairo.Context, size: int, text: str) -> None:
    """Draw the quote code with the same prominence as other applet labels."""
    draw_icon_label(cr=cr, text=text, size=size, max_width=size * 0.78)


def _draw_empty_state(*, cr: cairo.Context, size: int, failed: bool) -> None:
    """Draw loading or failed state when no sparkline points are available."""
    color = _WARN if failed else _LINE_FLAT
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(max(1.4, size * 0.04))
    cr.set_source_rgba(color[0], color[1], color[2], 0.85)
    y = size * 0.48
    cr.move_to(size * 0.24, y)
    cr.line_to(size * 0.76, y)
    cr.stroke()
    cr.restore()


def _draw_warning_badge(*, cr: cairo.Context, size: int) -> None:
    """Draw a small warning marker while stale chart data is still visible."""
    cr.save()
    radius = max(2.0, size * 0.075)
    cx = size * 0.79
    cy = size * 0.22
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.set_source_rgba(_WARN[0], _WARN[1], _WARN[2], 0.95)
    cr.fill_preserve()
    cr.set_line_width(max(0.8, size * 0.018))
    cr.set_source_rgba(0, 0, 0, 0.42)
    cr.stroke()
    cr.restore()
