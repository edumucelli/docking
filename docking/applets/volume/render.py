"""Pure Cairo renderer for the volume applet icon set."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from .state import _volume_icon_name


def _draw_speaker(
    *,
    cr: cairo.Context,
    size: int,
) -> None:
    """Draw filled two-tone speaker shape."""
    light_blue = (0.52, 0.75, 0.86)
    blue = (0.31, 0.67, 0.84)

    # Back rectangle (lighter blue)
    rx = size * 0.14
    ry = size * 0.32
    rw = size * 0.18
    rh = size * 0.36
    cr.rectangle(rx, ry, rw, rh)
    cr.set_source_rgb(*light_blue)
    cr.fill()

    # Front horn (main blue)
    left = rx + rw
    top = ry
    bottom = ry + rh
    tip_x = size * 0.54
    tip_top = size * 0.24
    tip_bottom = size * 0.76
    cr.new_path()
    cr.move_to(left, top)
    cr.line_to(tip_x, tip_top)
    cr.line_to(tip_x, tip_bottom)
    cr.line_to(left, bottom)
    cr.close_path()
    cr.set_source_rgb(*blue)
    cr.fill()


def _draw_waves(
    *,
    cr: cairo.Context,
    size: int,
    arcs: int,
) -> None:
    """Draw 1..3 sound waves to the right of the speaker."""
    if arcs <= 0:
        return
    cr.set_source_rgb(0.96, 0.83, 0.16)
    cr.set_line_width(max(1.0, size * 0.055))
    cr.set_line_cap(cairo.LINE_CAP_BUTT)

    cx = size * 0.54
    cy = size * 0.50
    base_r = size * 0.12
    step = size * 0.095
    start = -math.radians(62)
    end = math.radians(62)

    for i in range(arcs):
        radius = base_r + i * step
        cr.arc(cx, cy, radius, start, end)
        cr.stroke()


def _draw_mute_x(
    *,
    cr: cairo.Context,
    size: int,
) -> None:
    """Draw mute X marker."""
    cr.set_source_rgb(0.14, 0.22, 0.34)
    cr.set_line_width(max(1.2, size * 0.062))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cx = size * 0.80
    cy = size * 0.50
    arm = size * 0.07
    cr.move_to(cx - arm, cy - arm)
    cr.line_to(cx + arm, cy + arm)
    cr.move_to(cx - arm, cy + arm)
    cr.line_to(cx + arm, cy - arm)
    cr.stroke()


def create_volume_icon(
    *, size: int, volume: int, muted: bool
) -> GdkPixbuf.Pixbuf | None:
    """Render speaker icon with waves/mute marker for volume state."""
    icon = _volume_icon_name(volume=volume, muted=muted)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.set_source_rgba(0, 0, 0, 0)
    cr.paint()

    _draw_speaker(cr=cr, size=size)
    if icon == "audio-volume-muted":
        _draw_mute_x(cr=cr, size=size)
    elif icon == "audio-volume-low":
        _draw_waves(cr=cr, size=size, arcs=1)
    elif icon == "audio-volume-medium":
        _draw_waves(cr=cr, size=size, arcs=2)
    else:
        _draw_waves(cr=cr, size=size, arcs=3)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
