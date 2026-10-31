"""Black surfaces keep headless outputs capturable in desktop and app scenes."""

import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

windows = []


def rebuild(*unused):
    for window in windows:
        window.destroy()
    windows.clear()
    display = Gdk.Display.get_default()
    for i in range(display.get_n_monitors()):
        window = Gtk.Window()
        GtkLayerShell.init_for_window(window)
        GtkLayerShell.set_layer(window, GtkLayerShell.Layer.BACKGROUND)
        GtkLayerShell.set_monitor(window, display.get_monitor(i))
        GtkLayerShell.set_namespace(window, "lab-background")
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
        ):
            GtkLayerShell.set_anchor(window, edge, True)
        area = Gtk.DrawingArea()
        area.connect("draw", draw)
        window.add(area)
        window.show_all()
        windows.append(window)


def draw(widget, cr):
    cr.set_source_rgb(0, 0, 0)
    cr.paint()
    return False


def tick():
    for window in windows:
        window.queue_draw()
    return True


Gtk.init([])
if "--application" in sys.argv:
    # A normal client dismisses mobile shells' initial home/overview overlay.
    # Layer-shell backgrounds do not change that state, and TOP docks correctly
    # remain underneath the shell's overlay while it is open.
    GLib.set_prgname("lab-background")
    window = Gtk.Window(title="Compositor lab background")
    window.set_decorated(False)
    window.set_default_size(1280, 720)
    area = Gtk.DrawingArea()
    area.connect("draw", draw)
    window.add(area)
    window.show_all()
    window.maximize()
    windows.append(window)
else:
    Gdk.Screen.get_default().connect("monitors-changed", rebuild)
    rebuild()
GLib.timeout_add(200, tick)
Gtk.main()
