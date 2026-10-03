"""A minimal layer-shell panel, for testing whether a dock respects it.

The lab needs a panel that reserves exclusive-zone space on a chosen edge, so a
dock on the same edge can be checked for respecting it.

swaybar serves this on sway, but labwc, wayfire and cage ship no panel, so the
case could only ever run on one compositor. This probe is a real layer-shell
surface with a real exclusive zone that works anywhere `gtk-layer-shell` does,
which makes the case portable and lets the finding be reproduced against a
*different* panel implementation -- stronger evidence than repeating the same
one on a second compositor.

Paints a distinctive colour so it is unambiguous in a capture and cannot be
confused with the dock shelf or the desktop background.

Usage:
    panel_probe.py <thickness_px> [bottom|top|left|right]
"""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gtk, GtkLayerShell

# Distinct from the lab's black desktop and from the dock's light shelf.
PANEL_RGB = (0.12, 0.12, 0.30)


def _draw(widget: Gtk.DrawingArea, cr) -> bool:
    red, green, blue = PANEL_RGB
    cr.set_source_rgb(red, green, blue)
    cr.paint()
    return False


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    thickness = int(sys.argv[1])
    edge = sys.argv[2] if len(sys.argv) > 2 else "bottom"

    window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    GtkLayerShell.init_for_window(window)
    # A panel belongs in the top layer, above normal windows; a dock that
    # reserved the same edge should end up within what is left, not over it.
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.TOP)
    GtkLayerShell.set_namespace(window, "lab-panel")

    anchors = {
        "top": GtkLayerShell.Edge.TOP,
        "bottom": GtkLayerShell.Edge.BOTTOM,
        "left": GtkLayerShell.Edge.LEFT,
        "right": GtkLayerShell.Edge.RIGHT,
    }
    try:
        edge_id = anchors[edge]
    except KeyError:
        print(f"unknown edge: {edge}", file=sys.stderr)
        return 2

    for name, edge_id in anchors.items():
        GtkLayerShell.set_anchor(window, edge_id, name == edge)
    # Stretch along the main axis so it spans the whole edge, like a real panel.
    if edge in ("top", "bottom"):
        GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.RIGHT, True)
        window.set_size_request(-1, thickness)
    else:
        GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, True)
        window.set_size_request(thickness, -1)

    # The reservation under test.
    GtkLayerShell.set_exclusive_zone(window, thickness)

    area = Gtk.DrawingArea()
    area.connect("draw", _draw)
    window.add(area)
    window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
