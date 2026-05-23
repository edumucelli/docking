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

"""Pure Cairo rendering for the Crypto applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label
from docking.applets.crypto.state import (
    AssetType,
    CryptoPoint,
    CryptoSnapshot,
    percent_change,
)
from docking.applets.draw import rounded_rect
from docking.core.math import clamp

_BG_TOP = (0.05, 0.07, 0.11)
_BG_BOTTOM = (0.11, 0.12, 0.19)
_GRID = (1.0, 1.0, 1.0, 0.12)
_CYAN = (0.21, 0.78, 0.95)
_GREEN = (0.36, 0.88, 0.45)
_RED = (0.98, 0.34, 0.33)
_GOLD = (1.0, 0.72, 0.24)
_PURPLE = (0.62, 0.42, 1.0)
_TEXT = (1.0, 1.0, 1.0, 0.92)
_WARN = (0.95, 0.58, 0.20)


def render_icon(
    *,
    size: int,
    snapshot: CryptoSnapshot | None,
    asset_symbol: str,
    asset_type: AssetType = AssetType.COIN,
    fetch_failed: bool = False,
    pulse_phase: float | None = None,
) -> GdkPixbuf.Pixbuf | None:
    """Render a crypto/NFT sparkline icon."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    _draw_background(cr=cr, size=size)
    if asset_type == AssetType.NFT:
        _draw_nft_mark(cr=cr, size=size)
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

    _draw_symbol(cr=cr, size=size, text=asset_symbol.upper())
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _draw_background(*, cr: cairo.Context, size: int) -> None:
    pad = size * 0.06
    radius = size * 0.16
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
    cr.set_source_rgba(*_CYAN, 0.30)
    cr.stroke()


def _draw_nft_mark(*, cr: cairo.Context, size: int) -> None:
    cx = size * 0.28
    cy = size * 0.28
    radius = size * 0.14
    cr.save()
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.set_source_rgba(_PURPLE[0], _PURPLE[1], _PURPLE[2], 0.22)
    cr.fill_preserve()
    cr.set_line_width(max(1.0, size * 0.024))
    cr.set_source_rgba(_PURPLE[0], _PURPLE[1], _PURPLE[2], 0.72)
    cr.stroke()
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(max(6.0, size * 0.15))
    ext = cr.text_extents("N")
    cr.move_to(cx - ext.width / 2 - ext.x_bearing, cy + ext.height / 2)
    cr.set_source_rgba(1, 1, 1, 0.86)
    cr.show_text("N")
    cr.restore()


def _draw_grid(*, cr: cairo.Context, size: int) -> None:
    left = size * 0.15
    right = size * 0.87
    top = size * 0.32
    bottom = size * 0.70
    cr.set_line_width(max(0.7, size * 0.014))
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
    points: tuple[CryptoPoint, ...],
    pulse_phase: float | None = None,
) -> None:
    values = [point.price for point in points if point.price > 0]
    if not values:
        return

    left = size * 0.15
    right = size * 0.87
    top = size * 0.32
    bottom = size * 0.70
    min_price = min(values)
    max_price = max(values)
    span = max_price - min_price
    if span <= 0:
        span = max_price * 0.01 if max_price else 1.0
        min_price -= span / 2
        max_price += span / 2
        span = max_price - min_price

    coords: list[tuple[float, float]] = []
    for index, price in enumerate(values):
        x_frac = index / max(1, len(values) - 1)
        y_frac = (price - min_price) / span
        coords.append(
            (left + (right - left) * x_frac, bottom - (bottom - top) * y_frac)
        )

    change = percent_change(points)
    color = _CYAN
    if change is not None and change > 0.01:
        color = _GREEN
    elif change is not None and change < -0.01:
        color = _RED

    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(max(2.2, size * 0.06))
    cr.set_source_rgba(color[0], color[1], color[2], 0.23)
    _trace_line(cr=cr, coords=coords)
    cr.stroke()
    cr.set_line_width(max(1.2, size * 0.035))
    cr.set_source_rgb(*color)
    _trace_line(cr=cr, coords=coords)
    cr.stroke()
    cr.restore()

    last_x, last_y = coords[-1]
    dot_radius = max(1.7, size * 0.052)
    if pulse_phase is not None:
        phase = clamp(pulse_phase, 0.0, 1.0)
        cr.arc(last_x, last_y, dot_radius * (1.25 + phase), 0, math.tau)
        cr.set_source_rgba(color[0], color[1], color[2], 0.44 * (1.0 - phase))
        cr.fill()
    cr.arc(last_x, last_y, dot_radius, 0, math.tau)
    cr.set_source_rgb(*color)
    cr.fill_preserve()
    cr.set_line_width(max(0.8, size * 0.018))
    cr.set_source_rgba(1, 1, 1, 0.82)
    cr.stroke()


def _trace_line(*, cr: cairo.Context, coords: list[tuple[float, float]]) -> None:
    if not coords:
        return
    cr.move_to(*coords[0])
    for x, y in coords[1:]:
        cr.line_to(x, y)


def _draw_empty_state(*, cr: cairo.Context, size: int, failed: bool) -> None:
    color = _WARN if failed else _CYAN
    y = size * 0.52
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(max(1.4, size * 0.04))
    cr.set_source_rgba(color[0], color[1], color[2], 0.85)
    cr.move_to(size * 0.27, y)
    cr.line_to(size * 0.73, y)
    cr.stroke()
    cr.restore()


def _draw_warning_badge(*, cr: cairo.Context, size: int) -> None:
    radius = max(2.0, size * 0.07)
    cr.arc(size * 0.78, size * 0.24, radius, 0, math.tau)
    cr.set_source_rgba(*_WARN, 0.95)
    cr.fill()


def _draw_symbol(*, cr: cairo.Context, size: int, text: str) -> None:
    draw_icon_label(
        cr=cr,
        text=text[:5],
        size=size,
        max_width=size * 0.80,
        fill_rgba=_TEXT,
        outline_rgba=(0.0, 0.0, 0.0, 0.70),
    )
