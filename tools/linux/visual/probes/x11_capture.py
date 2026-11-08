"""Capture only a nested compositor's private Xvfb parent, never the host X server."""

import sys

import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

display = Gdk.Display.open(sys.argv[1])
if display is None:
    raise RuntimeError("private Xvfb display unavailable")
root = display.get_default_screen().get_root_window()
image = Gdk.pixbuf_get_from_window(root, 0, 0, root.get_width(), root.get_height())
image.savev(sys.argv[2], "png", [], [])
