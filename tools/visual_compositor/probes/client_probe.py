"""A real GTK Wayland client for input delivery and dodge tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk


def main() -> None:
    mode, path = sys.argv[1:3]
    GLib.set_prgname("lab-probe")
    window = Gtk.Window(title="Lab probe")
    window.set_default_size(640, 480)
    window.add_events(Gdk.EventMask.POINTER_MOTION_MASK)

    def motion(widget, event):
        Path(path).write_text(json.dumps({"x": event.x_root, "y": event.y_root}))
        return False

    def draw(widget, cr):
        cr.set_source_rgb(0.25, 0.25, 0.25)
        cr.paint()
        return False

    area = Gtk.DrawingArea()
    area.connect("draw", draw)
    window.add(area)
    window.connect("motion-notify-event", motion)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    if mode == "input":
        window.fullscreen()
    else:
        window.maximize()
    Gtk.main()


if __name__ == "__main__":
    main()
