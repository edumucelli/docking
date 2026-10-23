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

"""Procedural icon rendering for the URL Shortener applet.

The shortener icon needs to communicate "link" more than "network service".
Two interlocking chain links are a familiar, compact metaphor that still reads
well at dock sizes where fine detail would be lost.

This module keeps the icon procedural for the same reason other lightweight
applets do: the geometry is simple, scaling is important, and the rest of the
app can consume a pixbuf without caring whether it came from Cairo or from a
bundled asset.
"""

from __future__ import annotations

import math

import cairo
from gi.repository import Gdk, GdkPixbuf


def create_icon(size: int) -> GdkPixbuf.Pixbuf | None:
    """Render a chain-link icon: two interlocking stadium shapes at 45 deg."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    cx, cy = size / 2, size / 2
    # Each link is a horizontal stadium (pill) shape
    link_w = size * 0.28  # half-width of pill
    link_h = size * 0.11  # half-height of pill
    lw = max(size * 0.065, 1.5)
    angle = -math.pi / 4  # 45 degrees
    offset = size * 0.06  # how far apart the two link centers are

    cr.set_line_width(lw)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)

    # Left link (blue) - shifted upper-left
    cr.save()
    cr.translate(cx - offset, cy - offset)
    cr.rotate(angle)
    _pill(cr, link_w, link_h)
    cr.set_source_rgba(0.35, 0.55, 0.85, 0.95)
    cr.stroke()
    cr.restore()

    # Right link (teal) - shifted lower-right
    cr.save()
    cr.translate(cx + offset, cy + offset)
    cr.rotate(angle)
    _pill(cr, link_w, link_h)
    cr.set_source_rgba(0.25, 0.75, 0.65, 0.95)
    cr.stroke()
    cr.restore()

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _pill(cr: cairo.Context, hw: float, hh: float) -> None:
    """Draw a stadium/pill centered at origin with half-width hw, half-height hh."""
    r = hh
    cr.new_sub_path()
    cr.arc(hw - r, 0, r, -math.pi / 2, math.pi / 2)
    cr.arc(-(hw - r), 0, r, math.pi / 2, 3 * math.pi / 2)
    cr.close_path()
