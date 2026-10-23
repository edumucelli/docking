"""Import the installed .deb runtime using the wrapper's private paths."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--require-wayland", action="store_true")
args = parser.parse_args()

python_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
paths = [
    Path(f"/usr/lib/docking/vendor-python{python_minor}"),
    Path("/usr/lib/docking/python"),
    Path("/usr/lib/docking/vendor"),
]
sys.path[:0] = [str(path) for path in paths if path.is_dir()]

for module in ("openmeteo_requests", "requests_cache", "retry_requests", "docking.app"):
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
