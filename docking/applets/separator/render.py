"""Rendering helpers for Separator applet."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf  # noqa: E402

from .state import MIN_SIZE


def create_separator_icon(*, gap: int, size: int) -> GdkPixbuf.Pixbuf | None:
    w = max(MIN_SIZE, gap)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, w, size)
