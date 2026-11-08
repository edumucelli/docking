"""Check the installed runtime and desktop assets using the wrapper's paths."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import sys
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio

parser = argparse.ArgumentParser()
parser.add_argument("--require-wayland", action="store_true")
args = parser.parse_args()

python_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
paths = [
    Path(f"/usr/lib/docking/vendor-python{python_minor}"),
    Path("/usr/lib/docking/python"),
]
sys.path[:0] = [str(path) for path in paths if path.is_dir()]

assert Gio.SettingsSchemaSource.get_default() is not None, "Missing GSettings cache"
clock_svg = Path(
    "/usr/lib/docking/python/docking/assets/clock/Default/clock-drop-shadow.svg"
)
pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(str(clock_svg), 32, 32)
assert pixbuf.get_width() == 32 and pixbuf.get_height() == 32
print("Decoded the installed clock SVG and found the GSettings cache")

for module in ("docking.applets.weather.api", "docking.app"):
    imported = importlib.import_module(module)
    print(f"Imported {module} from {imported.__file__}")

if importlib.util.find_spec("pywayland") is not None:
    importlib.import_module("pywayland.client")
    print("Imported the PyWayland native client")
elif args.require_wayland:
    raise SystemExit("The native Wayland check requires an importable PyWayland client")
else:
    print("PyWayland unavailable on this host; live protocol features require it")

assert Path("/usr/bin/docking").is_file()
assert Path("/usr/share/applications/org.docking.Docking.desktop").is_file()
