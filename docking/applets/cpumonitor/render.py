"""Pure Cairo rendering for CPU monitor applet."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

from docking.applets.cpumonitor.state import cpu_hue_rgb

TWO_PI = 2 * math.pi
RADIUS_PERCENT = 0.9


def _render_gauge(cr: cairo.Context, size: int, cpu: float, mem: float) -> None:
    """Draw circular CPU gauge with memory arc (matching Plank)."""
    center = size / 2.0
    radius = center * RADIUS_PERCENT

    r, g, b = cpu_hue_rgb(cpu=cpu)
    base_alpha = 0.5
    cpu_clamped = max(0.001, min(cpu * 1.3, 1.0))

    # 1. Black underlay
    cr.arc(center, center, radius, 0, TWO_PI)
    cr.set_source_rgba(0, 0, 0, 0.5)
    cr.fill_preserve()

    # 2. Background color gradient (shade spreading to borders)
    bg = cairo.RadialGradient(center, center, 0, center, center, radius)
    bg.add_color_stop_rgba(0, r, g, b, base_alpha)
    bg.add_color_stop_rgba(0.2, r, g, b, base_alpha)
    bg.add_color_stop_rgba(1.0, r, g, b, 0.15)
    cr.set_source(bg)
    cr.fill_preserve()

    # 3. CPU indicator gradient (brighter core, scales with usage)
    ind = cairo.RadialGradient(center, center, 0, center, center, radius * cpu_clamped)
    ind.add_color_stop_rgba(0, r, g, b, 1.0)
    ind.add_color_stop_rgba(0.2, r, g, b, 1.0)
    edge_alpha = max(0.0, cpu * 1.3 - 1.0)
    ind.add_color_stop_rgba(1.0, r, g, b, edge_alpha)
    cr.set_source(ind)
    cr.fill()

    # 4. White highlight (gloss in upper portion)
    cr.arc(center, center * 0.8, center * 0.6, 0, TWO_PI)
    highlight = cairo.LinearGradient(0, 0, 0, center)
    highlight.add_color_stop_rgba(0, 1, 1, 1, 0.35)
    highlight.add_color_stop_rgba(1, 1, 1, 1, 0)
    cr.set_source(highlight)
    cr.fill()

    # 5. Double border rings
    cr.set_line_width(1.0)
    cr.arc(center, center, radius, 0, TWO_PI)
    cr.set_source_rgba(1, 1, 1, 0.75)
    cr.stroke()

    cr.set_line_width(1.0)
    cr.arc(center, center, radius - 1, 0, TWO_PI)
    cr.set_source_rgba(0.8, 0.8, 0.8, 0.75)
    cr.stroke()

    # 6. Memory arc (white, from 9 o'clock counter-clockwise)
    if mem > 0.001:
        cr.set_line_width(size / 32.0)
        cr.arc_negative(
            center,
            center,
            radius - 1,
            math.pi,
            math.pi - math.pi * 2.0 * mem,
        )
        cr.set_source_rgba(1, 1, 1, 0.85)
        cr.stroke()


def render_icon(size: int, cpu: float, mem: float) -> GdkPixbuf.Pixbuf | None:
    """Render CPU monitor gauge icon."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    _render_gauge(cr=cr, size=size, cpu=cpu, mem=mem)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
