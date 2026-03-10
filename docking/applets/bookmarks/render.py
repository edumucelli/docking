"""Pure Cairo rendering for bookmarks applet icon."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

from docking.applets.base import draw_icon_label
from docking.applets.draw import rounded_rect


def _draw_star(cr: cairo.Context, cx: float, cy: float, radius: float) -> None:
    """Draw a 5-pointed star path centered at (cx, cy)."""
    points: list[tuple[float, float]] = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        r = radius if i % 2 == 0 else radius * 0.4
        points.append((cx + r * math.cos(angle), cy - r * math.sin(angle)))
    cr.move_to(*points[0])
    for pt in points[1:]:
        cr.line_to(*pt)
    cr.close_path()


def render_icon(*, size: int, count: int) -> GdkPixbuf.Pixbuf | None:
    """Render a bookmark icon: blue rounded rect with white star + count badge."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    # Yellow rounded rectangle background
    margin = size * 0.08
    corner_radius = size * 0.18
    rounded_rect(
        cr=cr,
        x=margin,
        y=margin,
        width=size - 2 * margin,
        height=size - 2 * margin,
        radius=corner_radius,
    )
    cr.set_source_rgb(0.95, 0.75, 0.15)
    cr.fill()

    # White 5-pointed star, centered vertically with slight downward shift
    star_radius = size * 0.28
    _draw_star(cr=cr, cx=size / 2, cy=size * 0.52, radius=star_radius)
    cr.set_source_rgba(1, 1, 1, 0.95)
    cr.fill()

    # Count badge via draw_icon_label
    if count > 0:
        draw_icon_label(cr=cr, text=str(count), size=size)

    # Convert to pixbuf
    pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
    return pixbuf
