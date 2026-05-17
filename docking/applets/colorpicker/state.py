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

"""Pure helpers for Color Picker applet."""

from __future__ import annotations

import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk


def pick_pixel(x: int, y: int) -> tuple[int, int, int] | None:
    """Read a single pixel from the root window at screen coordinates."""
    root = Gdk.get_default_root_window()
    if root is None:
        return None
    pb = Gdk.pixbuf_get_from_window(root, x, y, 1, 1)
    if pb is None:
        return None
    p = pb.get_pixels()
    return (p[0], p[1], p[2])


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB to uppercase hex string."""
    return f"#{r:02X}{g:02X}{b:02X}"
