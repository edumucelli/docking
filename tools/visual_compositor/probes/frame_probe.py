"""Black background surfaces keep otherwise empty headless outputs capturable."""

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
Gdk.Screen.get_default().connect("monitors-changed", rebuild)
rebuild()
GLib.timeout_add(200, tick)
Gtk.main()
