"""Calibrate real native output pixels in the private Xvfb parent capture."""

import json
import os
import sys
from pathlib import Path

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib
from kwin_outputs import snapshot
from output_calibration import measure_output, remove_markers

lab = Path(os.environ["LAB_DIR"])
outputs = snapshot()
display = Gdk.Display.open((lab / "outer-display").read_text().strip())
if display is None:
    raise RuntimeError("private Xvfb unavailable")
root = display.get_default_screen().get_root_window()
image = Gdk.pixbuf_get_from_window(root, 0, 0, root.get_width(), root.get_height())
image.savev(sys.argv[1] + ".parent.png", "png", [], [])
pixels, stride, channels = (
    image.get_pixels(),
    image.get_rowstride(),
    image.get_n_channels(),
)
parent_rect = {"x": 0, "y": 0, "width": root.get_width(), "height": root.get_height()}
left, top = min(o["x"] for o in outputs), min(o["y"] for o in outputs)
width = max(o["x"] + o["width"] for o in outputs) - left
height = max(o["y"] + o["height"] for o in outputs) - top
canvas = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, width, height)
canvas.fill(0x000000FF)
allocations = json.loads((lab / "frame-allocations.json").read_text())
observations = []
for output in outputs:
    observation = allocations[f"{output['x']}:{output['y']}"]
    if observation["size"] != [output["width"], output["height"]]:
        raise RuntimeError(
            "calibration surface has not reached full native output size"
        )
    colors = observation["colors"]
    rect = measure_output(pixels, stride, channels, parent_rect, colors=colors)
    cleaned = remove_markers(pixels, stride, channels, rect, colors=colors)
    clean_image = GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(cleaned),
        GdkPixbuf.Colorspace.RGB,
        image.get_has_alpha(),
        8,
        image.get_width(),
        image.get_height(),
        stride,
    )
    patch = clean_image.new_subpixbuf(
        rect["x"], rect["y"], rect["width"], rect["height"]
    )
    patch = patch.scale_simple(
        output["width"], output["height"], GdkPixbuf.InterpType.BILINEAR
    )
    if not patch.get_has_alpha():
        patch = patch.add_alpha(False, 0, 0, 0)
    patch.copy_area(
        0,
        0,
        output["width"],
        output["height"],
        canvas,
        output["x"] - left,
        output["y"] - top,
    )
    observations.append(
        {"output": output, "rendered_rect": rect, "calibration": observation}
    )
canvas.savev(sys.argv[1], "png", [], [])
Path(sys.argv[1] + ".capture.json").write_text(json.dumps(observations, indent=2))
