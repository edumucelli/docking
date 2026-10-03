"""Black background surfaces keep otherwise empty headless outputs capturable."""

import json
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell
from output_calibration import marker_colors

windows = []
allocations = {}


def rebuild(*unused):
    for window in windows:
        window.destroy()
    windows.clear()
    allocations.clear()
    display = Gdk.Display.get_default()
    for i in range(display.get_n_monitors()):
        window = Gtk.Window()
        GtkLayerShell.init_for_window(window)
        GtkLayerShell.set_layer(window, GtkLayerShell.Layer.BACKGROUND)
        GtkLayerShell.set_monitor(window, display.get_monitor(i))
        GtkLayerShell.set_namespace(window, "lab-background")
        GtkLayerShell.set_exclusive_zone(window, -1)
        for edge in (
            GtkLayerShell.Edge.TOP,
            GtkLayerShell.Edge.BOTTOM,
            GtkLayerShell.Edge.LEFT,
            GtkLayerShell.Edge.RIGHT,
        ):
            GtkLayerShell.set_anchor(window, edge, True)
        area = Gtk.DrawingArea()
        area.connect("draw", draw, display.get_monitor(i), i)
        window.add(area)
        window.show_all()
        windows.append(window)


def draw(widget, cr, monitor, index):
    path = Path(os.environ["LAB_DIR"]) / "background.json"
    color = json.loads(path.read_text()) if path.exists() else [0, 0, 0]
    cr.set_source_rgb(*(value / 255 for value in color))
    cr.paint()
    if os.environ.get("LAB_FRAME_MARKERS") == "1":
        allocation = widget.get_allocation()
        rect = monitor.get_geometry()
        allocations[f"{rect.x}:{rect.y}"] = {
            "size": [allocation.width, allocation.height],
            "colors": marker_colors(index),
        }
        state = Path(os.environ["LAB_DIR"]) / "frame-allocations.json"
        temporary = state.with_suffix(".tmp")
        temporary.write_text(json.dumps(allocations))
        temporary.replace(state)
        corners = [
            (0, 0),
            (allocation.width - 12, 0),
            (0, allocation.height - 12),
            (allocation.width - 12, allocation.height - 12),
        ]
        colors = marker_colors(index)
        for (x, y), color in zip(corners, colors, strict=True):
            cr.set_source_rgb(*(value / 255 for value in color))
            cr.rectangle(x, y, 12, 12)
            cr.fill()
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
