"""Pure Cairo rendering for the Thermals applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label
from docking.applets.temperature import TemperatureUnit, format_temperature_compact
from docking.applets.thermals.state import (
    ThermalSnapshot,
    thermal_color,
    thermal_level,
)


def render_icon(
    *,
    size: int,
    snapshot: ThermalSnapshot | None,
    loading: bool = False,
    error: bool = False,
    temperature_unit: TemperatureUnit = TemperatureUnit.CELSIUS,
) -> GdkPixbuf.Pixbuf | None:
    """Render thermals icon with a shared bottom temperature label."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    temp = snapshot.hottest.celsius if snapshot and snapshot.hottest else None
    error = error or bool(snapshot and (not snapshot.available or snapshot.error))
    r, g, b = thermal_color(temp)

    _draw_background(cr=cr, size=size, rgb=(r, g, b), error=error, loading=loading)
    _draw_thermometer(cr=cr, size=size, temp=temp, rgb=(r, g, b))

    draw_icon_label(
        cr=cr,
        text=(
            "..."
            if loading and snapshot is None
            else format_temperature_compact(
                temp,
                temperature_unit=temperature_unit,
            )
        ),
        size=size,
    )

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _draw_background(
    *,
    cr: cairo.Context,
    size: int,
    rgb: tuple[float, float, float],
    error: bool,
    loading: bool,
) -> None:
    if error:
        color = (0.34, 0.36, 0.40)
    elif loading:
        color = rgb
    else:
        color = rgb

    cx = size * 0.50
    cy = size * 0.38
    radius = size * 0.40
    gradient = cairo.RadialGradient(cx, cy, radius * 0.10, cx, cy, radius)
    gradient.add_color_stop_rgba(0.0, color[0], color[1], color[2], 0.44)
    gradient.add_color_stop_rgba(0.62, color[0], color[1], color[2], 0.14)
    gradient.add_color_stop_rgba(1.0, color[0], color[1], color[2], 0.00)
    cr.set_source(gradient)
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.fill()


def _draw_thermometer(
    *,
    cr: cairo.Context,
    size: int,
    temp: float | None,
    rgb: tuple[float, float, float],
) -> None:
    cx = size * 0.50
    top = size * 0.10
    bottom = size * 0.57
    tube_w = size * 0.18
    fill_w = size * 0.075
    bulb_r = size * 0.16

    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    cr.set_line_width(max(4.0, tube_w * 1.14))
    cr.set_source_rgba(0.05, 0.06, 0.08, 0.46)
    cr.move_to(cx, top)
    cr.line_to(cx, bottom)
    cr.stroke()
    cr.arc(cx, bottom, bulb_r * 1.14, 0, math.tau)
    cr.fill()

    cr.set_line_width(max(3.0, tube_w))
    cr.set_source_rgba(0.95, 0.97, 0.98, 0.96)
    cr.move_to(cx, top)
    cr.line_to(cx, bottom)
    cr.stroke()
    cr.arc(cx, bottom, bulb_r, 0, math.tau)
    cr.fill()

    cr.set_line_width(max(1.0, size * 0.025))
    cr.set_source_rgba(0.07, 0.09, 0.11, 0.56)
    cr.move_to(cx, top)
    cr.line_to(cx, bottom)
    cr.stroke()
    cr.arc(cx, bottom, bulb_r, 0, math.tau)
    cr.stroke()

    fill_top = bottom - (bottom - top) * thermal_level(temp)
    cr.set_line_width(max(2.0, fill_w))
    cr.set_source_rgba(rgb[0], rgb[1], rgb[2], 1.0)
    cr.move_to(cx, fill_top)
    cr.line_to(cx, bottom)
    cr.stroke()
    cr.arc(cx, bottom, bulb_r * 0.66, 0, math.tau)
    cr.fill()

    cr.set_line_width(max(1.0, size * 0.018))
    cr.set_source_rgba(0.07, 0.09, 0.11, 0.26)
    for ratio in (0.20, 0.42, 0.64):
        y = bottom - (bottom - top) * ratio
        cr.move_to(cx + tube_w * 0.42, y)
        cr.line_to(cx + tube_w * 0.78, y)
        cr.stroke()

    cr.set_source_rgba(1, 1, 1, 0.58)
    cr.arc(cx - bulb_r * 0.28, bottom - bulb_r * 0.30, bulb_r * 0.18, 0, math.tau)
    cr.fill()
