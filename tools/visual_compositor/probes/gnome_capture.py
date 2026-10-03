"""Capture each actual Mutter output and assemble a logical-coordinate canvas."""

import json
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib

geometry = json.loads(
    subprocess.check_output(
        [sys.executable, str(Path(__file__).with_name("gnome_probe.py")), "geometry"]
    )
)
outputs = geometry["outputs"]
left, top = min(o["x"] for o in outputs), min(o["y"] for o in outputs)
width = max(o["x"] + o["width"] for o in outputs) - left
height = max(o["y"] + o["height"] for o in outputs) - top
canvas = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, width, height)
canvas.fill(0x000000FF)
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
for index, output in enumerate(outputs):
    path = str(Path(sys.argv[1]).with_suffix(f".output-{index}.png"))
    result = bus.call_sync(
        "org.docking.VisualLab.Gnome",
        "/org/docking/VisualLab/Gnome",
        "org.docking.VisualLab.Gnome1",
        "CaptureOutput",
        GLib.Variant("(us)", (index, path)),
        GLib.VariantType.new("(b)"),
        Gio.DBusCallFlags.NONE,
        5000,
        None,
    )
    if not result.unpack()[0]:
        raise RuntimeError("native Mutter output capture failed")
    image = GdkPixbuf.Pixbuf.new_from_file(path)
    scaled = image.scale_simple(
        output["width"], output["height"], GdkPixbuf.InterpType.BILINEAR
    )
    scaled.copy_area(
        0,
        0,
        output["width"],
        output["height"],
        canvas,
        output["x"] - left,
        output["y"] - top,
    )
    Path(path).unlink()
canvas.savev(sys.argv[1], "png", [], [])
