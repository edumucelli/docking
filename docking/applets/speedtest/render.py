"""Pure Cairo rendering for speedtest applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label
from docking.applets.speedtest.state import format_speed, speed_tier

# Visible tick positions along the dial (0.0..1.0).
_TICK_STEPS = (0.0, 0.25, 0.5, 0.75, 1.0)

# Color per tier for the needle.
_TIER_COLORS: dict[str, tuple[float, float, float]] = {
    "none": (0.55, 0.55, 0.60),
    "slow": (0.90, 0.35, 0.25),
    "medium": (0.97, 0.55, 0.15),
    "fast": (0.30, 0.75, 0.35),
}

# Four-band gradient painted along the dial, left to right: red, orange,
# yellow, green. Each tuple is (fraction_start, fraction_end, rgb).
_DIAL_BANDS: tuple[tuple[float, float, tuple[float, float, float]], ...] = (
    (0.00, 0.25, (0.90, 0.30, 0.25)),
    (0.25, 0.50, (0.95, 0.55, 0.15)),
    (0.50, 0.75, (0.97, 0.82, 0.20)),
    (0.75, 1.00, (0.30, 0.75, 0.40)),
)

# Download speed that corresponds to the needle's right edge. Tuned so a home
# fiber result lands near the end of the dial without clipping.
_NEEDLE_FULL_SCALE_MBPS = 500.0


def _dial_center(size: int) -> tuple[float, float, float]:
    """Return (cx, cy, radius) of the speedometer dial."""
    cx = size / 2
    cy = size * 0.60
    radius = size * 0.38
    return cx, cy, radius


def _draw_dial(cr: cairo.Context, size: int) -> None:
    cx, cy, radius = _dial_center(size)

    # Dark base track so the colored bands sit on a crisp ring.
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_BUTT)
    cr.set_line_width(max(3.5, size * 0.12))
    cr.set_source_rgba(0.14, 0.14, 0.18, 0.95)
    cr.arc(cx, cy, radius, math.pi, math.tau)
    cr.stroke()
    cr.restore()

    # Four colored bands (red -> orange -> yellow -> green).
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_BUTT)
    cr.set_line_width(max(2.5, size * 0.09))
    for start_frac, end_frac, (r, g, b) in _DIAL_BANDS:
        start_angle = math.pi + start_frac * math.pi
        end_angle = math.pi + end_frac * math.pi
        cr.set_source_rgb(r, g, b)
        cr.arc(cx, cy, radius, start_angle, end_angle)
        cr.stroke()
    cr.restore()

    # Tick marks along the arc.
    cr.save()
    cr.set_source_rgba(1, 1, 1, 0.85)
    cr.set_line_width(max(1.0, size * 0.025))
    for step in _TICK_STEPS:
        angle = math.pi + step * math.pi
        inner = radius - size * 0.07
        outer = radius + size * 0.01
        x1 = cx + math.cos(angle) * inner
        y1 = cy + math.sin(angle) * inner
        x2 = cx + math.cos(angle) * outer
        y2 = cy + math.sin(angle) * outer
        cr.move_to(x1, y1)
        cr.line_to(x2, y2)
        cr.stroke()
    cr.restore()


def _draw_needle(
    cr: cairo.Context,
    size: int,
    *,
    download_mbps: float,
    tier: str,
) -> None:
    cx, cy, radius = _dial_center(size)
    fraction = max(0.0, min(1.0, download_mbps / _NEEDLE_FULL_SCALE_MBPS))
    angle = math.pi + fraction * math.pi

    length = radius - size * 0.04
    tip_x = cx + math.cos(angle) * length
    tip_y = cy + math.sin(angle) * length

    r, g, b = _TIER_COLORS[tier]
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_width(max(1.6, size * 0.05))
    cr.set_source_rgba(r, g, b, 1.0)
    cr.move_to(cx, cy)
    cr.line_to(tip_x, tip_y)
    cr.stroke()
    cr.restore()

    # Central pivot.
    cr.save()
    cr.set_source_rgba(1, 1, 1, 0.95)
    cr.arc(cx, cy, max(1.5, size * 0.05), 0, math.tau)
    cr.fill()
    cr.set_source_rgba(0.18, 0.18, 0.22, 0.95)
    cr.arc(cx, cy, max(0.8, size * 0.03), 0, math.tau)
    cr.fill()
    cr.restore()


def render_icon(
    *,
    size: int,
    download_mbps: float | None,
    label: str = "",
) -> GdkPixbuf.Pixbuf | None:
    """Render a speedometer dial with an optional badge label."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    tier = speed_tier(download_mbps) if download_mbps is not None else "none"
    _draw_dial(cr=cr, size=size)
    needle_value = download_mbps if download_mbps is not None else 0.0
    _draw_needle(cr=cr, size=size, download_mbps=needle_value, tier=tier)
    if not label and download_mbps is not None:
        label = format_speed(download_mbps)
    if label:
        draw_icon_label(cr=cr, text=label, size=size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
