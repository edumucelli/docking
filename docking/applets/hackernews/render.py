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

"""Rendering helpers for the Hacker News applet icon."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Pango, PangoCairo

from docking.applets.draw import rounded_rect
from docking.applets.hackernews.state import HackerNewsStory


def _draw_centered_text(
    cr: cairo.Context,
    *,
    text: str,
    x: float,
    y: float,
    width: float,
    height: float,
    font: str,
    r: float,
    g: float,
    b: float,
    alpha: float = 1.0,
) -> None:
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription(font))
    layout.set_text(text, -1)
    _, logical = layout.get_pixel_extents()
    tx = x + (width - logical.width) / 2 - logical.x
    ty = y + (height - logical.height) / 2 - logical.y
    cr.set_source_rgba(r, g, b, alpha)
    cr.move_to(tx, ty)
    PangoCairo.show_layout(cr, layout)


def _score_label(story: HackerNewsStory | None) -> str:
    if story is None:
        return "--"
    if story.score >= 1000:
        return f"{story.score // 1000}k"
    return str(story.score)


def render_icon(
    *,
    size: int,
    story: HackerNewsStory | None = None,
    index: int = 0,
    count: int = 0,
    loading: bool = False,
    error: bool = False,
) -> GdkPixbuf.Pixbuf | None:
    """Render a compact Hacker News icon.

    The icon deliberately stays source-specific for now: orange HN tile, large
    ``Y`` mark, current headline rank, and a score/comment strip.  Scroll and
    menu actions change the current story; the small rank/score changes make
    that visible even when the full headline only fits in the tooltip/menu.
    """
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    pad = size * 0.07
    radius = size * 0.18
    rounded_rect(
        cr=cr,
        x=pad,
        y=pad,
        width=size - pad * 2,
        height=size - pad * 2,
        radius=radius,
    )
    if error:
        cr.set_source_rgba(0.52, 0.12, 0.10, 0.96)
    elif loading:
        cr.set_source_rgba(0.92, 0.44, 0.02, 0.78)
    else:
        cr.set_source_rgba(1.0, 0.40, 0.0, 0.96)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.28)
    cr.set_line_width(max(1.0, size * 0.035))
    cr.stroke()

    # Subtle top highlight.
    cr.save()
    rounded_rect(
        cr=cr,
        x=pad + size * 0.03,
        y=pad + size * 0.03,
        width=size - pad * 2 - size * 0.06,
        height=(size - pad * 2) * 0.48,
        radius=radius * 0.82,
    )
    cr.set_source_rgba(1, 1, 1, 0.10)
    cr.fill()
    cr.restore()

    _draw_centered_text(
        cr,
        text="Y",
        x=0,
        y=size * 0.08,
        width=size,
        height=size * 0.50,
        font=f"Sans Bold {max(1, int(size * 0.48))}px",
        r=1,
        g=1,
        b=1,
    )

    if count:
        rank = f"{max(1, index + 1)}"
    elif loading:
        rank = "..."
    else:
        rank = "HN"

    strip_h = size * 0.25
    strip_y = size - pad - strip_h
    rounded_rect(
        cr=cr,
        x=pad + size * 0.06,
        y=strip_y,
        width=size - pad * 2 - size * 0.12,
        height=strip_h,
        radius=size * 0.08,
    )
    cr.set_source_rgba(0.04, 0.04, 0.05, 0.52)
    cr.fill()

    _draw_centered_text(
        cr,
        text=rank,
        x=pad + size * 0.06,
        y=strip_y,
        width=(size - pad * 2 - size * 0.12) * 0.45,
        height=strip_h,
        font=f"Sans Bold {max(1, int(size * 0.18))}px",
        r=1,
        g=1,
        b=1,
    )
    _draw_centered_text(
        cr,
        text=_score_label(story),
        x=pad + size * 0.06 + (size - pad * 2 - size * 0.12) * 0.42,
        y=strip_y,
        width=(size - pad * 2 - size * 0.12) * 0.58,
        height=strip_h,
        font=f"Sans Bold {max(1, int(size * 0.16))}px",
        r=1,
        g=1,
        b=1,
        alpha=0.92,
    )

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
