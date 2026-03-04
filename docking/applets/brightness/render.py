"""Cairo icon rendering for Brightness applet."""

from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

from docking.applets.base import draw_icon_label

TWO_PI = 2 * math.pi
_NUM_RAYS = 8

# Colors from the reference icon
_YELLOW = (0.98, 0.85, 0.40)
_ORANGE = (0.96, 0.65, 0.20)
_BLUE = (0.55, 0.75, 0.95)


def create_icon(
    size: int,
    brightness: float,
    show_level: bool = False,
) -> GdkPixbuf.Pixbuf | None:
    """Render a sun icon — left half yellow, right half blue.

    The split moves with brightness: 100% = all yellow, 0% = all blue.
    Rays on the bright side are yellow, dim side orange.
    """
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    cx = size / 2
    cy = size * 0.45
    b = max(0.0, min(1.0, brightness))

    disc_r = size * 0.24
    ray_len = size * 0.10
    ray_w = max(2.0, size * 0.07)
    ray_inner = disc_r + size * 0.05
    ray_outer = ray_inner + ray_len

    # The split x-position: brightness 1.0 = full right, 0.0 = full left
    split_x = cx - disc_r + 2 * disc_r * b

    # Draw rays as thick lines (rectangles with round cap)
    cr.set_line_width(ray_w)
    cr.set_line_cap(cairo.LINE_CAP_BUTT)

    for i in range(_NUM_RAYS):
        angle = TWO_PI * i / _NUM_RAYS - math.pi / 2
        x0 = cx + math.cos(angle) * ray_inner
        y0 = cy + math.sin(angle) * ray_inner
        x1 = cx + math.cos(angle) * ray_outer
        y1 = cy + math.sin(angle) * ray_outer

        # Rays on bright side = yellow, dim side = orange
        mid_x = (x0 + x1) / 2
        if mid_x < split_x:
            cr.set_source_rgb(*_YELLOW)
        else:
            cr.set_source_rgb(*_ORANGE)

        cr.move_to(x0, y0)
        cr.line_to(x1, y1)
        cr.stroke()

    # Disc — left half yellow, right half blue, split by brightness
    # Yellow half (clip left of split)
    cr.save()
    cr.rectangle(0, 0, split_x, size)
    cr.clip()
    cr.arc(cx, cy, disc_r, 0, TWO_PI)
    cr.set_source_rgb(*_YELLOW)
    cr.fill()
    cr.restore()

    # Blue half (clip right of split)
    cr.save()
    cr.rectangle(split_x, 0, size, size)
    cr.clip()
    cr.arc(cx, cy, disc_r, 0, TWO_PI)
    cr.set_source_rgb(*_BLUE)
    cr.fill()
    cr.restore()

    # Level label at bottom
    if show_level:
        draw_icon_label(cr=cr, text=f"{int(brightness * 100)}%", size=size)

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
