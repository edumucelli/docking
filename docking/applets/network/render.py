"""Rendering helpers for Network applet icon."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, Pango, PangoCairo  # noqa: E402

from docking.applets.base import load_theme_icon
from docking.applets.network.state import format_speed, signal_to_icon


def create_icon(
    size: int,
    is_connected: bool,
    is_wifi: bool,
    signal_strength: int,
    rx_speed: float,
    tx_speed: float,
) -> GdkPixbuf.Pixbuf | None:
    """Load network icon with optional speed overlay."""
    icon_name = signal_to_icon(
        strength=signal_strength,
        is_connected=is_connected,
        is_wifi=is_wifi,
    )
    base = load_theme_icon(name=icon_name, size=size)

    if not base or not is_connected:
        return base

    # Overlay speed text
    rx_str = format_speed(bps=rx_speed)
    tx_str = format_speed(bps=tx_speed)
    if tx_speed > 1024:
        overlay = f"\u2193{rx_str.split()[0]} \u2191{tx_str.split()[0]}"
    else:
        overlay = f"\u2193{rx_str.split()[0]}"

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    Gdk.cairo_set_source_pixbuf(cr, base, 0, 0)
    cr.paint()

    font_size = max(1, int(size * 0.18))
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription(f"Sans Bold {font_size}px"))
    layout.set_text(overlay, -1)

    _ink, logical = layout.get_pixel_extents()
    tx = (size - logical.width) / 2 - logical.x
    ty = size - logical.height - max(1, size * 0.02) - logical.y

    cr.move_to(tx, ty)
    PangoCairo.layout_path(cr, layout)
    cr.set_source_rgba(0, 0, 0, 0.8)
    cr.set_line_width(max(1.5, size * 0.04))
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.stroke_preserve()
    cr.set_source_rgba(1, 1, 1, 1)
    cr.fill()

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
