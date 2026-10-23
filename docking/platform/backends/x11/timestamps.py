"""Obtain an X server timestamp for actions outside a GTK input callback."""

from __future__ import annotations

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkX11, Gtk


def activation_timestamp() -> int:
    timestamp = Gtk.get_current_event_time()
    if timestamp:
        return timestamp
    if not isinstance(Gdk.Display.get_default(), GdkX11.X11Display):
        return 0
    # The server-time request needs property notifications on a client window.
    # A private input-only window avoids changing another widget's event mask.
    probe = Gtk.Invisible()
    try:
        probe.realize()
        window = probe.get_window()
        if window is None:
            return 0
        window.set_events(window.get_events() | Gdk.EventMask.PROPERTY_CHANGE_MASK)
        # GTK 3 takes a Gdk.Window; the installed stubs describe GTK 4 surfaces.
        return GdkX11.x11_get_server_time(window)  # ty: ignore[invalid-argument-type]
    finally:
        probe.destroy()
