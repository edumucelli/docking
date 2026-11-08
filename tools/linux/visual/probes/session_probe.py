"""Report what a live Wayland session actually offers.

Used by adapters for compositors where the answer decides which Docking backend
runs and cannot be known from configuration alone -- whether the compositor
implements layer-shell. Guessing would mean the harness asserts against a
backend the compositor never selects.

The probe connects as a plain GTK client to whatever session it is started in,
so it must run after the compositor is up and with WAYLAND_DISPLAY/GDK_BACKEND
already exported.

Usage:
    session_probe.py capabilities
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk


def capabilities() -> int:
    Gtk.init([])
    display = Gdk.Display.get_default()
    wayland = type(display).__name__ == "GdkWaylandDisplay"

    layer_shell = False
    if wayland:
        try:
            gi.require_version("GtkLayerShell", "0.1")
            from gi.repository import GtkLayerShell

            layer_shell = bool(GtkLayerShell.is_supported())
        except Exception:
            layer_shell = False

    overlap = False
    if "COSMIC" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("input_probe.py")),
                "--globals",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            overlap = "zcosmic_overlap_notify_v1" in json.loads(result.stdout)
    json.dump(
        {
            "gtk_display_is_wayland": wayland,
            "cosmic_overlap_supported": overlap,
            "gtk_display": type(display).__name__ if display else None,
            "layer_shell_supported": layer_shell,
        },
        sys.stdout,
        indent=2,
    )
    print()
    return 0


def geometry() -> int:
    Gtk.init([])
    display = Gdk.Display.get_default()
    outputs = []
    for index in range(display.get_n_monitors()):
        monitor = display.get_monitor(index)
        rect = monitor.get_geometry()
        outputs.append(
            {
                "name": f"output-{index}",
                "x": rect.x,
                "y": rect.y,
                "width": rect.width,
                "height": rect.height,
                "scale": monitor.get_scale_factor(),
            }
        )
    print(json.dumps({"outputs": outputs, "dock_rect": None}))
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "geometry":
        return geometry()
    if len(sys.argv) > 1 and sys.argv[1] == "capabilities":
        return capabilities()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
