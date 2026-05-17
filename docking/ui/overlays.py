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

"""Shared Cairo overlay helpers for badges and progress bars."""

from __future__ import annotations

import math

import cairo

from docking.applets.draw import rounded_rect
from docking.core.theme import RGB, RGBA


def draw_circle_badge(
    *,
    cr: cairo.Context,
    cx: float,
    cy: float,
    radius: float,
    background_rgba: RGBA,
    outline_rgba: RGBA | None = None,
    outline_width: float = 0.0,
) -> None:
    """Draw a filled circular badge, with optional outline."""
    cr.arc(cx, cy, radius, 0, math.tau)
    cr.set_source_rgba(*background_rgba)
    if outline_rgba is not None and outline_width > 0:
        cr.fill_preserve()
        cr.set_source_rgba(*outline_rgba)
        cr.set_line_width(outline_width)
        cr.stroke()
        return
    cr.fill()


def draw_count_badge(
    *,
    cr: cairo.Context,
    x: float,
    y: float,
    width: float,
    height: float,
    badge_count: int,
    background_rgba: RGBA = (0.89, 0.17, 0.19, 1.0),
    text_rgba: RGBA = (1.0, 1.0, 1.0, 1.0),
    font_family: str = "Sans",
) -> None:
    """Draw a rounded count badge with centered text."""
    text = "99+" if badge_count > 99 else str(badge_count)
    corner = min(height / 2, max(height * 0.35, 4.0))

    cr.save()
    cr.select_font_face(font_family, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    if badge_count <= 9:
        font_size = height * 0.58
    elif badge_count <= 99:
        font_size = height * 0.48
    else:
        font_size = height * 0.38
    cr.set_font_size(max(7.0, font_size))
    ext = cr.text_extents(text)
    ascent, descent, *_ = cr.font_extents()

    rounded_rect(cr=cr, x=x, y=y, width=width, height=height, radius=corner)
    cr.set_source_rgba(*background_rgba)
    cr.fill()

    center_x = x + width / 2
    center_y = y + height / 2
    cr.set_source_rgba(*text_rgba)
    cr.move_to(
        center_x - (ext.width / 2 + ext.x_bearing),
        center_y + (ascent - descent) / 2,
    )
    cr.show_text(text)
    cr.restore()


def draw_progress_bar(
    *,
    cr: cairo.Context,
    x: float,
    y: float,
    width: float,
    height: float,
    progress: float,
    color: RGB | RGBA,
) -> None:
    """Draw a high-contrast rounded progress bar."""
    clamped = max(0.0, min(1.0, progress))
    corner = height / 2
    red, green, blue = color[:3]
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    if luminance < 0.45:
        fill_color = (0.24, 0.80, 0.98, 0.98)
    else:
        fill_color = (red, green, blue, 0.98)

    cr.save()
    rounded_rect(cr=cr, x=x, y=y, width=width, height=height, radius=corner)
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.22)
    cr.fill()

    rounded_rect(cr=cr, x=x, y=y, width=width, height=height, radius=corner)
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.35)
    cr.set_line_width(max(1.0, height * 0.10))
    cr.stroke()

    if clamped > 0.0:
        rounded_rect(
            cr=cr,
            x=x,
            y=y,
            width=width * clamped,
            height=height,
            radius=corner,
        )
        cr.set_source_rgba(*fill_color)
        cr.fill()
    cr.restore()
