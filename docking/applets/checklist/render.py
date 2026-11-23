"""Draw a checklist icon with the number of unchecked items."""

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.base import draw_icon_label
from docking.applets.draw import rounded_rect


def render_icon(*, size: int, unchecked: int = 0) -> GdkPixbuf.Pixbuf | None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.save()
    cr.scale(size / 64, size / 64)

    rounded_rect(cr=cr, x=9, y=4, width=46, height=54, radius=8)
    cr.set_source_rgb(0.20, 0.36, 0.60)
    cr.fill()
    rounded_rect(cr=cr, x=13, y=8, width=38, height=46, radius=5)
    cr.set_source_rgb(0.93, 0.96, 1.0)
    cr.fill()

    cr.set_line_width(2.5)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_source_rgb(0.08, 0.43, 0.30)
    cr.move_to(19, 20)
    cr.line_to(22, 23)
    cr.line_to(27, 17)
    cr.stroke()
    cr.set_source_rgb(0.26, 0.38, 0.53)
    cr.rectangle(19, 32, 8, 8)
    cr.stroke()
    for y in (20, 36):
        cr.move_to(33, y)
        cr.line_to(44, y)
        cr.stroke()
    cr.restore()

    if unchecked:
        draw_icon_label(cr, str(unchecked) if unchecked <= 99 else "99+", size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
