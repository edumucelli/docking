"""Pure Cairo + theme rendering for weather applet icon."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

from docking.applets.base import draw_icon_label, load_theme_icon
from docking.applets.weather.api import WeatherData
from docking.applets.weather.state import DEFAULT_ICON_NAME


def create_icon(
    *,
    size: int,
    weather: WeatherData | None,
    show_temperature: bool,
) -> GdkPixbuf.Pixbuf | None:
    """Create weather icon, optionally overlaying current temperature."""
    icon_name = weather.icon_name if weather else DEFAULT_ICON_NAME
    base = load_theme_icon(name=icon_name, size=size)
    if not base or not weather or not show_temperature:
        return base

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    Gdk.cairo_set_source_pixbuf(cr, base, 0, 0)
    cr.paint()
    draw_icon_label(cr=cr, text=f"{weather.temperature:.0f}°", size=size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
