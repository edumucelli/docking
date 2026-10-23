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

"""Cairo icon rendering for Moon applet.

Draws a moon disc with illumination. The lit portion is rendered as a
clipped fill - no external image assets needed. The original Cairo-Dock
Moon applet used pre-rendered GIF images (moon00a.gif through moon10b.gif)
from the briancasey.org website. This version renders the moon purely in
Cairo, giving smooth scaling at any icon size.
"""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label

# Moon colors
_LIT = (0.95, 0.93, 0.82)  # warm white/cream for lit surface
_DARK = (0.18, 0.20, 0.25)  # dark grey-blue for shadowed surface
_OUTLINE = (0.55, 0.55, 0.50)  # subtle edge


def create_icon(
    size: int,
    illumination: float,
    waning: bool = False,
    label: str | None = None,
) -> GdkPixbuf.Pixbuf | None:
    """Render a moon disc at the given illumination level.

    illumination: 0.0 (new moon) to 1.0 (full moon).
    waning: if True, lit side is on the left (waning); else right (waxing).
    label: optional text overlay at bottom (e.g. phase name).
    """
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    cx = size / 2
    cy = size * 0.45
    radius = size * 0.32
    illum = max(0.0, min(1.0, illumination))

    # Dark disc (shadow)
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.set_source_rgb(*_DARK)
    cr.fill()

    # Lit portion - use a terminator curve (ellipse clip)
    # The terminator is the boundary between lit and dark.
    # At illumination 0.5 the terminator is a straight vertical line.
    # At 1.0 or 0.0 the terminator matches the disc edge.
    #
    # We draw the lit half as: one semicircle (always lit edge) +
    # one half-ellipse (terminator, width varies with illumination).
    if illum > 0.001:
        cr.save()
        # Clip to disc
        cr.arc(cx, cy, radius, 0, math.tau)
        cr.clip()

        # The lit side semicircle
        if waning:
            # Lit on left: semicircle from 90° to 270° (left half)
            start_angle = math.pi / 2
            end_angle = 3 * math.pi / 2
            # Terminator bulges right for waning
            term_dir = 1.0
        else:
            # Lit on right: semicircle from -90° to 90° (right half)
            start_angle = -math.pi / 2
            end_angle = math.pi / 2
            # Terminator bulges left for waxing
            term_dir = -1.0

        cr.new_path()
        # Semicircle (the always-lit edge)
        cr.arc(cx, cy, radius, start_angle, end_angle)

        # Terminator half-ellipse back to start
        # Width of ellipse: at illum=0.5 it's 0 (straight line),
        # at illum=1.0 it's full radius (full circle),
        # at illum=0.0 it would be -radius (inverted, but we skip).
        term_width = radius * (2.0 * illum - 1.0) * term_dir

        # Draw terminator as a bezier approximation of half-ellipse
        # from bottom of semicircle back to top
        if waning:
            cr.curve_to(
                cx + term_width,
                cy + radius * 0.55,
                cx + term_width,
                cy - radius * 0.55,
                cx,
                cy - radius,
            )
        else:
            cr.curve_to(
                cx + term_width,
                cy + radius * 0.55,
                cx + term_width,
                cy - radius * 0.55,
                cx,
                cy - radius,
            )

        cr.close_path()
        cr.set_source_rgb(*_LIT)
        cr.fill()
        cr.restore()

    # Thin outline
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.set_source_rgba(*_OUTLINE, 0.4)
    cr.set_line_width(max(1.0, size * 0.02))
    cr.stroke()

    if label:
        draw_icon_label(cr=cr, text=label, size=size)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
