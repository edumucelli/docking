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

"""Procedural icon rendering for the Unit Converter applet.

The visual goal is not to depict every supported unit type. Instead the icon
needs to say "conversion" quickly at small sizes. Two opposing arrows do that
better than a literal ruler/scale/clock mash-up would.

Why Cairo is a good fit here

A conversion icon is mostly geometry: lines, arrow heads, and color contrast.
Drawing it procedurally keeps it crisp at multiple dock sizes and avoids adding
another static asset whose only job would be to encode a simple symbol.

The top and bottom arrows use different colors so the bidirectional nature of
conversion is legible even when the icon is very small.
"""

from __future__ import annotations

import cairo
from gi.repository import Gdk, GdkPixbuf


def create_icon(size: int) -> GdkPixbuf.Pixbuf | None:
    """Render a bidirectional arrow icon representing unit conversion."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    cx = size / 2
    cy = size / 2
    arrow_len = size * 0.3
    head = size * 0.1
    gap = size * 0.14

    cr.set_line_width(max(1.5, size * 0.04))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)

    # Top arrow: left to right (blue)
    cr.set_source_rgba(0.35, 0.55, 0.85, 0.95)
    y_top = cy - gap
    cr.move_to(cx - arrow_len, y_top)
    cr.line_to(cx + arrow_len, y_top)
    cr.stroke()
    cr.move_to(cx + arrow_len - head, y_top - head)
    cr.line_to(cx + arrow_len, y_top)
    cr.line_to(cx + arrow_len - head, y_top + head)
    cr.stroke()

    # Bottom arrow: right to left (teal)
    cr.set_source_rgba(0.25, 0.75, 0.65, 0.95)
    y_bot = cy + gap
    cr.move_to(cx + arrow_len, y_bot)
    cr.line_to(cx - arrow_len, y_bot)
    cr.stroke()
    cr.move_to(cx - arrow_len + head, y_bot - head)
    cr.line_to(cx - arrow_len, y_bot)
    cr.line_to(cx - arrow_len + head, y_bot + head)
    cr.stroke()

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
