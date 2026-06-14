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

"""Cairo rendering for the Last.fm applet icon and album art."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib

# Last.fm's brand red is too saturated as a background; a deeper variant reads
# better next to icon-themed art.
_LASTFM_RED = (0.85, 0.10, 0.16)
_LASTFM_RED_DARK = (0.55, 0.05, 0.08)

_CORNER_RADIUS_RATIO = 0.15


def render_default_icon(size: int) -> GdkPixbuf.Pixbuf | None:
    """Cairo-drawn Last.fm-styled placeholder icon (no album art available)."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    pad = max(1.0, size * 0.06)
    radius = size * _CORNER_RADIUS_RATIO
    grad = cairo.LinearGradient(0, pad, 0, size - pad)
    grad.add_color_stop_rgb(0, *_LASTFM_RED)
    grad.add_color_stop_rgb(1, *_LASTFM_RED_DARK)
    _rounded_rect(
        cr=cr,
        x=pad,
        y=pad,
        w=size - 2 * pad,
        h=size - 2 * pad,
        radius=radius,
    )
    cr.set_source(grad)
    cr.fill()

    # Centered "fm" wordmark.
    cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    font_size = max(8, int(size * 0.42))
    cr.set_font_size(font_size)
    text = "fm"
    extents = cr.text_extents(text)
    tx = (size - extents.width) / 2 - extents.x_bearing
    ty = (size + extents.height) / 2 - extents.y_bearing - extents.height
    cr.move_to(tx, ty)
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
    cr.show_text(text)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def pixbuf_from_bytes(data: bytes, size: int) -> GdkPixbuf.Pixbuf | None:
    """Decode raw image bytes into a square pixbuf at the requested size."""
    if not data:
        return None
    try:
        loader = GdkPixbuf.PixbufLoader()
        loader.set_size(size, size)
        loader.write(data)
        loader.close()
    except GLib.Error:
        return None
    pixbuf = loader.get_pixbuf()
    if pixbuf is None:
        return None
    if pixbuf.get_width() != size or pixbuf.get_height() != size:
        scaled = pixbuf.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
        if scaled is not None:
            return scaled
    return pixbuf


def round_pixbuf_corners(pixbuf: GdkPixbuf.Pixbuf) -> GdkPixbuf.Pixbuf:
    """Return a copy of ``pixbuf`` clipped to a rounded-corner shape."""
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    radius = min(width, height) * _CORNER_RADIUS_RATIO

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    cr = cairo.Context(surface)
    _rounded_rect(cr=cr, x=0, y=0, w=width, h=height, radius=radius)
    cr.clip()
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.paint()
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, width, height)


def _rounded_rect(
    *, cr: cairo.Context, x: float, y: float, w: float, h: float, radius: float
) -> None:
    cr.new_sub_path()
    cr.arc(x + w - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + w - radius, y + h - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + h - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()
