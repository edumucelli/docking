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

"""Pure Cairo rendering for the APOD applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib

from docking.ui.overlays import draw_warning_badge


def _rounded_rect_path(
    cr: cairo.Context,
    *,
    x: float,
    y: float,
    w: float,
    h: float,
    r: float,
) -> None:
    cr.new_path()
    cr.move_to(x + r, y)
    cr.line_to(x + w - r, y)
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.line_to(x + w, y + h - r)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.line_to(x + r, y + h)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.line_to(x, y + r)
    cr.arc(x + r, y + r, r, math.pi, -math.pi / 2)
    cr.close_path()


def _draw_fallback(cr: cairo.Context, size: int) -> None:
    """Draw a starry-night placeholder when no image is available."""
    cr.save()
    _rounded_rect_path(cr, x=0, y=0, w=size, h=size, r=size * 0.12)
    cr.clip()

    # Deep night gradient.
    pat = cairo.LinearGradient(0, 0, 0, size)
    pat.add_color_stop_rgb(0.0, 0.03, 0.04, 0.15)
    pat.add_color_stop_rgb(1.0, 0.12, 0.08, 0.30)
    cr.set_source(pat)
    cr.rectangle(0, 0, size, size)
    cr.fill()

    # A handful of stars at deterministic positions (no RNG seeding needed).
    star_positions = (
        (0.22, 0.18, 0.90),
        (0.68, 0.25, 0.75),
        (0.42, 0.46, 1.00),
        (0.82, 0.58, 0.80),
        (0.18, 0.72, 0.65),
        (0.55, 0.78, 0.85),
    )
    for fx, fy, brightness in star_positions:
        radius = size * 0.02
        cr.set_source_rgba(1, 1, 1, brightness)
        cr.arc(fx * size, fy * size, radius, 0, math.tau)
        cr.fill()
    cr.restore()

    # Subtle border.
    _rounded_rect_path(cr, x=0, y=0, w=size, h=size, r=size * 0.12)
    cr.set_source_rgba(1, 1, 1, 0.15)
    cr.set_line_width(max(1.0, size * 0.02))
    cr.stroke()


def _load_cover_pixbuf(*, path: str, size: int) -> GdkPixbuf.Pixbuf | None:
    """Load ``path`` scaled and center-cropped to fill a ``size x size`` tile.

    Equivalent to CSS ``object-fit: cover``: the smaller dimension is
    scaled to match ``size``; the overflow on the larger dimension is
    trimmed symmetrically so the image always fills the square tile.
    """
    try:
        info = GdkPixbuf.Pixbuf.get_file_info(path)
    except GLib.Error:
        return _fallback_scaled_pixbuf(path=path, size=size)
    if info is None or len(info) < 3:
        return _fallback_scaled_pixbuf(path=path, size=size)
    fmt, src_w, src_h = info[0], info[1], info[2]
    if fmt is None or src_w <= 0 or src_h <= 0:
        return _fallback_scaled_pixbuf(path=path, size=size)

    scale = max(size / src_w, size / src_h)
    target_w = max(size, round(src_w * scale))
    target_h = max(size, round(src_h * scale))
    try:
        scaled = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, target_w, target_h, True)
    except GLib.Error:
        return None
    if scaled is None:
        return None
    offset_x = max(0, (scaled.get_width() - size) // 2)
    offset_y = max(0, (scaled.get_height() - size) // 2)
    cropped = scaled.new_subpixbuf(offset_x, offset_y, size, size)
    return cropped or scaled


def _fallback_scaled_pixbuf(*, path: str, size: int) -> GdkPixbuf.Pixbuf | None:
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
    except GLib.Error:
        return None


def _paint_cover(cr: cairo.Context, *, pixbuf: GdkPixbuf.Pixbuf, size: int) -> None:
    pb_w = pixbuf.get_width()
    pb_h = pixbuf.get_height()
    # Center any residual mismatch (should be zero for cover-sized pixbufs).
    offset_x = (size - pb_w) / 2
    offset_y = (size - pb_h) / 2
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, offset_x, offset_y)
    cr.paint()


def render_icon(
    *,
    size: int,
    cached_path: str,
    warning: bool = False,
) -> GdkPixbuf.Pixbuf | None:
    """Render the thumbnail as a rounded-square icon."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    pixbuf: GdkPixbuf.Pixbuf | None = None
    if cached_path:
        pixbuf = _load_cover_pixbuf(path=cached_path, size=size)

    if pixbuf is None:
        _draw_fallback(cr=cr, size=size)
        if warning:
            draw_warning_badge(cr=cr, size=size)
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    # Clip to a rounded square and paint the image edge-to-edge, no backdrop.
    cr.save()
    _rounded_rect_path(cr, x=0, y=0, w=size, h=size, r=size * 0.12)
    cr.clip()
    _paint_cover(cr=cr, pixbuf=pixbuf, size=size)
    cr.restore()
    if warning:
        draw_warning_badge(cr=cr, size=size)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
