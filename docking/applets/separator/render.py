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

"""Rendering helpers for Separator applet."""

from __future__ import annotations

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf

from .state import MIN_SIZE


def create_separator_icon(*, gap: int, size: int) -> GdkPixbuf.Pixbuf | None:
    w = max(MIN_SIZE, gap)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, size)
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, w, size)
