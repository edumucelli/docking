"""Original blue paper-plane artwork drawn directly with Cairo."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from docking.applets.draw import rounded_rect
from docking.applets.webapp_state import BrowserPhase
from docking.ui.overlays import draw_warning_badge


def render_icon(
    *, size: int, phase: BrowserPhase = BrowserPhase.READY
) -> GdkPixbuf.Pixbuf | None:
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    rounded_rect(
        cr=cr,
        x=size * 0.06,
        y=size * 0.06,
        width=size * 0.88,
        height=size * 0.88,
        radius=size * 0.24,
    )
    gradient = cairo.LinearGradient(0, 0, 0, size)
    gradient.add_color_stop_rgb(0, 0.26, 0.74, 0.96)
    gradient.add_color_stop_rgb(1, 0.08, 0.45, 0.77)
    cr.set_source(gradient)
    cr.fill()
    cr.set_source_rgb(0.98, 0.99, 1)
    cr.move_to(size * 0.18, size * 0.46)
    cr.line_to(size * 0.79, size * 0.23)
    cr.line_to(size * 0.67, size * 0.78)
    cr.line_to(size * 0.47, size * 0.62)
    cr.line_to(size * 0.36, size * 0.72)
    cr.line_to(size * 0.36, size * 0.54)
    cr.close_path()
    cr.fill()
    cr.set_source_rgb(0.68, 0.85, 0.96)
    cr.move_to(size * 0.36, size * 0.54)
    cr.line_to(size * 0.71, size * 0.31)
    cr.line_to(size * 0.47, size * 0.62)
    cr.line_to(size * 0.36, size * 0.72)
    cr.close_path()
    cr.fill()
    if phase in {BrowserPhase.ERROR, BrowserPhase.OFFLINE, BrowserPhase.UNAVAILABLE}:
        draw_warning_badge(cr=cr, size=size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
