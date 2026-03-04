"""Pure Cairo rendering for Network applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

from docking.applets.base import draw_icon_label

_BG_ARC = (0.72, 0.84, 0.97)
_FG = (0.42, 0.64, 0.90)
_DOT_DISCONNECTED = (0.70, 0.70, 0.70)


def _draw_wifi_icon(
    *,
    cr: cairo.Context,
    size: int,
    connected: bool,
    active_arcs: int,
) -> None:
    """Draw wifi arcs with active level and disconnected marker."""
    cx = size * 0.50
    cy = size * 0.74

    widths = [size * 0.19, size * 0.30, size * 0.41]
    line_w = max(1.4, size * 0.075)
    start = math.radians(215)
    end = math.radians(325)

    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(line_w)

    # Background arcs
    cr.set_source_rgb(*_BG_ARC)
    for radius in widths:
        cr.arc(cx, cy, radius, start, end)
        cr.stroke()

    # Active arcs from inner to outer
    if connected and active_arcs > 0:
        cr.set_source_rgb(*_FG)
        for idx in range(min(active_arcs, len(widths))):
            cr.arc(cx, cy, widths[idx], start, end)
            cr.stroke()

    dot_radius = size * 0.10
    if connected:
        cr.set_source_rgb(*_FG)
    else:
        cr.set_source_rgb(*_DOT_DISCONNECTED)
    cr.arc(cx, cy, dot_radius, 0.0, math.tau)
    cr.fill()

    # Disconnected marker "x"
    if not connected:
        cr.set_source_rgb(*_FG)
        cross = size * 0.07
        off_x = size * 0.11
        off_y = size * 0.05
        cr.set_line_width(max(1.6, size * 0.05))
        cr.move_to(cx + off_x - cross, cy + off_y - cross)
        cr.line_to(cx + off_x + cross, cy + off_y + cross)
        cr.move_to(cx + off_x - cross, cy + off_y + cross)
        cr.line_to(cx + off_x + cross, cy + off_y - cross)
        cr.stroke()


def _rounded_rect(
    *,
    cr: cairo.Context,
    x: float,
    y: float,
    w: float,
    h: float,
    r: float,
) -> None:
    r = max(0.0, min(r, min(w, h) / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _draw_wired_icon(*, cr: cairo.Context, size: int, connected: bool) -> None:
    """Draw ethernet/RJ45-like glyph."""
    x = size * 0.18
    y = size * 0.24
    w = size * 0.64
    h = size * 0.50
    radius = size * 0.07

    cr.set_source_rgb(*_FG if connected else _BG_ARC)
    _rounded_rect(cr=cr, x=x, y=y, w=w, h=h, r=radius)
    cr.set_line_width(max(1.6, size * 0.065))
    cr.stroke()

    pin_w = w * 0.10
    pin_h = h * 0.22
    pin_gap = w * 0.045
    pin_start_x = x + (w - (pin_w * 4 + pin_gap * 3)) / 2
    pin_y = y + h * 0.10
    cr.set_source_rgb(*_FG if connected else _BG_ARC)
    for i in range(4):
        px = pin_start_x + i * (pin_w + pin_gap)
        cr.rectangle(px, pin_y, pin_w, pin_h)
        cr.fill()

    # Cable stem
    stem_w = size * 0.10
    stem_h = size * 0.16
    stem_x = x + (w - stem_w) / 2
    stem_y = y + h
    cr.rectangle(stem_x, stem_y, stem_w, stem_h)
    cr.fill()


def create_icon(
    size: int,
    is_connected: bool,
    is_wifi: bool,
    signal_strength: int,
    rx_speed: float,
    tx_speed: float,
    speed_overlay: str = "none",
) -> GdkPixbuf.Pixbuf | None:
    """Create standardized network icon independent from system theme.

    speed_overlay: "download", "upload", or "none".
    """
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()

    if is_wifi:
        if not is_connected:
            active_arcs = 0
        elif signal_strength >= 80:
            active_arcs = 3
        elif signal_strength >= 60:
            active_arcs = 2
        elif signal_strength >= 40:
            active_arcs = 1
        else:
            active_arcs = 0
        _draw_wifi_icon(
            cr=cr,
            size=size,
            connected=is_connected,
            active_arcs=active_arcs,
        )
    else:
        _draw_wired_icon(cr=cr, size=size, connected=is_connected)

    if is_connected and speed_overlay == "download" and rx_speed > 0:
        draw_icon_label(cr=cr, text=f"\u2193{_short(bps=rx_speed)}", size=size)
    elif is_connected and speed_overlay == "upload" and tx_speed > 0:
        draw_icon_label(cr=cr, text=f"\u2191{_short(bps=tx_speed)}", size=size)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _short(bps: float) -> str:
    """Compact speed value without unit suffix (e.g. '1.2M')."""
    if bps < 1024:
        return f"{bps:.0f}B"
    if bps < 1024 * 1024:
        return f"{bps / 1024:.0f}K"
    if bps < 1024 * 1024 * 1024:
        return f"{bps / (1024 * 1024):.1f}M"
    return f"{bps / (1024 * 1024 * 1024):.1f}G"
