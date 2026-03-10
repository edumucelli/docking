"""Pure Cairo rendering for quick note sticky icon."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

from docking.applets.draw import rounded_rect


def render_icon(*, size: int, has_content: bool) -> GdkPixbuf.Pixbuf | None:
    """Render a yellow sticky note icon with optional text lines."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)

    margin = size * 0.08
    w = size - 2 * margin
    h = size - 2 * margin
    r = size * 0.08
    fold = size * 0.18

    # Yellow sticky background
    rounded_rect(cr=cr, x=margin, y=margin, width=w, height=h, radius=r)
    cr.set_source_rgb(1.0, 0.93, 0.55)
    cr.fill()

    # Corner fold (top-right triangle)
    fx = margin + w - fold
    fy = margin
    cr.move_to(fx, fy)
    cr.line_to(margin + w, fy + fold)
    cr.line_to(margin + w, fy)
    cr.close_path()
    cr.set_source_rgb(0.85, 0.78, 0.40)
    cr.fill()

    # Fold crease line
    cr.move_to(fx, fy)
    cr.line_to(margin + w, fy + fold)
    cr.set_source_rgba(0.6, 0.55, 0.3, 0.6)
    cr.set_line_width(max(1.0, size * 0.02))
    cr.stroke()

    # Horizontal text lines
    line_alpha = 0.55 if has_content else 0.22
    cr.set_source_rgba(0.3, 0.28, 0.15, line_alpha)
    lw = max(1.0, size * 0.025)
    cr.set_line_width(lw)

    line_x0 = margin + w * 0.15
    line_x1 = margin + w * 0.85
    line_starts = [0.35, 0.48, 0.61, 0.74]
    for frac in line_starts:
        ly = margin + h * frac
        cr.move_to(line_x0, ly)
        cr.line_to(line_x1, ly)
        cr.stroke()

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
