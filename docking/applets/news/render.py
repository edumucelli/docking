"""Cairo rendering helpers for the News RSS applet."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Pango, PangoCairo

from docking.applets.draw import rounded_rect
from docking.ui.overlays import draw_warning_badge


def _draw_centered_text(
    cr: cairo.Context,
    *,
    text: str,
    x: float,
    y: float,
    width: float,
    height: float,
    font: str,
    alpha: float = 1.0,
) -> None:
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription.from_string(font))
    layout.set_text(text, -1)
    _, logical = layout.get_pixel_extents()
    cr.set_source_rgba(1, 1, 1, alpha)
    cr.move_to(
        x + (width - logical.width) / 2 - logical.x,
        y + (height - logical.height) / 2 - logical.y,
    )
    PangoCairo.show_layout(cr, layout)


def render_icon(
    *,
    size: int,
    index: int = 0,
    count: int = 0,
    loading: bool = False,
    error: bool = False,
) -> GdkPixbuf.Pixbuf | None:
    """Render a neutral news-reader tile with headline position."""
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
    if loading:
        cr.set_source_rgba(0.20, 0.48, 0.72, 0.76)
    else:
        cr.set_source_rgba(0.10, 0.38, 0.68, 0.97)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.30)
    cr.set_line_width(max(1.0, size * 0.035))
    cr.stroke()

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
    _draw_centered_text(
        cr,
        text="N",
        x=0,
        y=size * 0.07,
        width=size,
        height=size * 0.52,
        font=f"Sans Bold {max(1, int(size * 0.40))}px",
    )

    label = f"{max(1, index + 1)}" if count else ("..." if loading else "RSS")
    strip_x = pad + size * 0.06
    strip_w = size - pad * 2 - size * 0.12
    strip_h = size * 0.25
    strip_y = size - pad - strip_h
    rounded_rect(
        cr=cr,
        x=strip_x,
        y=strip_y,
        width=strip_w,
        height=strip_h,
        radius=size * 0.08,
    )
    cr.set_source_rgba(0.04, 0.04, 0.05, 0.52)
    cr.fill()
    _draw_centered_text(
        cr,
        text=label,
        x=strip_x,
        y=strip_y,
        width=strip_w,
        height=strip_h,
        font=f"Sans Bold {max(1, int(size * 0.18))}px",
        alpha=0.94,
    )
    if error:
        draw_warning_badge(cr=cr, size=size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
