"""Check a bundled interpreter, native resources, applets, and helper processes."""

from __future__ import annotations

import argparse
import inspect
import os
import subprocess
import sys
from pathlib import Path

from tools.windows.bundle import extract_bundle, payload_root


def _foreign_msys_modules(
    paths: list[str], *, root: Path, bundled_helper: Path, deployed_helper: Path
) -> tuple[list[str], str]:
    """Exempt only the exact authenticated deployment of the bundled helper."""
    import hashlib
    import tempfile

    source = bundled_helper.resolve(strict=True)
    assert source.is_relative_to(root.resolve(strict=True))
    assert source.name == "input-thread-64.dll"
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    expected = (
        Path(tempfile.gettempdir())
        / "Docking"
        / "input-runtime"
        / checksum
        / source.name
    )
    assert deployed_helper.absolute() == expected.absolute(), (
        "Input helper is outside its content-addressed runtime cache"
    )
    assert not deployed_helper.is_symlink()
    deployed = deployed_helper.resolve(strict=True)
    assert hashlib.sha256(deployed.read_bytes()).hexdigest() == checksum, (
        "Deployed input helper differs from the bundled source"
    )
    return (
        [
            name
            for name in paths
            if "msys64" in name.casefold() and Path(name).resolve() != deployed
        ],
        checksum,
    )


PROBE = r"""
import ctypes
import gettext
import importlib
import json
import ssl
import sys
from pathlib import Path

root = Path(sys.prefix).resolve()
assert (root / "share/docking/runtime.json").is_file()
assert sys.flags.isolated == 1
assert sys.flags.utf8_mode == 1
assert all(Path(entry).resolve().is_relative_to(root) for entry in sys.path)
from docking.launcher import prepare_display
assert prepare_display()
print("Initializing GTK", flush=True)
import gi
for name, version in (("Gtk", "3.0"), ("Gdk", "3.0"),
                      ("GdkPixbuf", "2.0"), ("Gst", "1.0")):
    gi.require_version(name, version)
from gi.repository import Gdk, GdkPixbuf, Gst, Gtk
assert Gtk.init_check()[0]
assert Gdk.Display.get_default().__gtype__.name == "GdkWin32Display"
import docking
assets = Path(docking.__file__).parent / "assets"
translation = gettext.translation(
    "docking", localedir=assets.parent / "locale", languages=["fr"])
assert translation.gettext("Clock") == "Horloge"
print("Loading SVG and PNG assets", flush=True)
svg = GdkPixbuf.Pixbuf.new_from_file(str(assets / "poof.svg"))
png = GdkPixbuf.Pixbuf.new_from_file(str(assets / "icons/applet-fallback.png"))
assert svg.get_width() > 0 and png.get_width() > 0
print("Loading theme icons", flush=True)
assert Gtk.IconTheme.get_default().load_icon("folder", 32, 0).get_width() > 0
print("Initializing GStreamer", flush=True)
Gst.init(None)
print("Checking media factories and playback", flush=True)
factories = ("playbin", "oggdemux", "vorbisdec", "audiotestsrc", "wasapi2sink")
for name in factories:
    assert Gst.ElementFactory.find(name) is not None, name
pipeline = Gst.parse_launch("audiotestsrc num-buffers=3 ! fakesink")
try:
    assert pipeline.set_state(Gst.State.PLAYING) != Gst.StateChangeReturn.FAILURE
    message = pipeline.get_bus().timed_pop_filtered(
        5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    assert message is not None and message.type == Gst.MessageType.EOS
finally:
    pipeline.set_state(Gst.State.NULL)
modules = ("docking", "gi", "cairo", "numpy", "PIL.Image", "psutil",
           "openmeteo_requests", "requests_cache", "retry_requests")
print("Importing Python runtime dependencies", flush=True)
for name in modules:
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(root), name
import certifi
ssl.create_default_context(cafile=certifi.where())
from docking.applets import get_applet_catalog, load_applet_class
print("Discovering applets", flush=True)
catalog = get_applet_catalog()
for name in ("clock", "weather", "ambient", "camshield"):
    assert name in catalog and load_applet_class(name) is not None, name
from docking.applets.ambient.applet import _build_file_pipeline
from docking.applets.ambient.state import FILE_SOUNDS, NOISE_WAVES
print("Decoding every bundled Ambient sound and both noise modes", flush=True)
pipelines = []
for sound in FILE_SOUNDS:
    player = _build_file_pipeline(assets / "sounds" / (sound.name + ".ogg"), 0.5)
    assert player is not None, sound.name
    sink = Gst.ElementFactory.make("fakesink")
    sink.set_property("sync", False)
    player.set_property("audio-sink", sink)
    pipelines.append((sound.name, player))
for name, wave in NOISE_WAVES.items():
    pipelines.append((name, Gst.parse_launch(
        f"audiotestsrc wave={wave} num-buffers=3 ! volume volume=0.5 ! fakesink")))
for name, player in pipelines:
    try:
        assert player.set_state(Gst.State.PLAYING) != Gst.StateChangeReturn.FAILURE
        message = player.get_bus().timed_pop_filtered(
            10 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        assert message is not None and message.type == Gst.MessageType.EOS, name
    finally:
        player.set_state(Gst.State.NULL)
from docking.platform.backends.windows.input_thread import Request, _runtime
print("Loading the packaged input-thread helper", flush=True)
helpers = Path(docking.__file__).parent / "platform/backends/windows"
for bits in (64, 32):
    assert (helpers / f"input-thread-{bits}.dll").is_file()
deployed_helper = _runtime(64)
helper = ctypes.WinDLL(str(deployed_helper))
helper.ProtocolSize.restype = ctypes.c_uint32
assert helper.ProtocolSize() == ctypes.sizeof(Request)
print("Querying the packaged GTK input thread", flush=True)
import threading
import time
from docking.applets.keyboardlayout.windows import WindowsKeyboardAPI
from docking.platform.backends.windows.ffi import window_handle
from docking.platform.backends.windows.input_thread import InputThread
from gi.repository import GLib
fixture = Gtk.Window(title="Docking owned packaged input fixture")
fixture.add(Gtk.Entry())
fixture.show_all()
api = WindowsKeyboardAPI()
loop = GLib.MainContext.default()
deadline = time.monotonic() + 5
context = None
while context is None and time.monotonic() < deadline:
    loop.iteration(False)
    window = fixture.get_window()
    handle = window_handle(window) if window is not None else None
    context = api._context_for_root(handle) if handle else None
    if context is None:
        time.sleep(0.005)
assert context is not None and context.pid == __import__("os").getpid()
values, errors, cancelled = [], [], threading.Event()
def inspect_input():
    try:
        values.append(InputThread().request(context, cancelled))
    except Exception as error:
        errors.append(error)
worker = threading.Thread(target=inspect_input, daemon=True)
try:
    worker.start()
    deadline = time.monotonic() + 10
    while worker.is_alive() and time.monotonic() < deadline:
        loop.iteration(False)
        time.sleep(0.005)
    assert not worker.is_alive(), "Packaged input helper did not complete"
    if errors:
        raise errors[0]
    assert values and values[0] is not None and values[0].layout
finally:
    cancelled.set()
    worker.join(3)
    fixture.destroy()
from docking.applets.camshield.windows_client import WindowsCameraAPI
camera = WindowsCameraAPI()
print("Checking the camera helper", flush=True)
try:
    snapshot = camera.snapshot()
finally:
    camera.stop()
from docking.platform.backends.windows.webview import loader_path
print("Checking the WebView2 loader and loaded DLL paths", flush=True)
loader = loader_path()
assert loader.is_relative_to(root)
ctypes.WinDLL(str(loader)).GetAvailableCoreWebView2BrowserVersionString
import psutil
__DLL_PROVENANCE__
foreign, helper_sha256 = _foreign_msys_modules(
    [entry.path for entry in psutil.Process().memory_maps()],
    root=root, bundled_helper=helpers / "input-thread-64.dll",
    deployed_helper=deployed_helper)
assert not foreign, foreign
report = {"ok": True, "python": sys.version.split()[0],
          "gtk": [Gtk.get_major_version(), Gtk.get_minor_version(),
                  Gtk.get_micro_version()],
          "gstreamer": Gst.version_string(), "applets": sorted(catalog),
          "camera_helper": True, "webview2_loader": True,
          "authenticated_input_helper_sha256": helper_sha256,
          "foreign_msys_modules": foreign}
Path(sys.argv[1]).write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
"""
PROBE = PROBE.replace("__DLL_PROVENANCE__", inspect.getsource(_foreign_msys_modules))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    installation = parser.add_mutually_exclusive_group(required=True)
    installation.add_argument("--bundle", type=Path)
    installation.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires a native Windows desktop")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle = payload_root(
        extract_bundle(args.archive, output / "extracted/Docking")
        if args.archive is not None
        else args.bundle.resolve()
    )
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith(("PYTHON", "GTK_", "GDK_", "GI_", "GST_")) or name in {
            "MSYSTEM",
            "MINGW_PREFIX",
            "PYGI_DLL_PATH",
            "XDG_DATA_DIRS",
            "FONTCONFIG_PATH",
        }:
            environment.pop(name)
    environment["PATH"] = str(Path(os.environ["WINDIR"]) / "System32")
    environment["PYTHONPATH"] = str(output / "nonexistent-source-checkout")
    environment["XDG_CACHE_HOME"] = str(output / "cache")
    with (output / "preflight.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(
            [
                str(bundle / "runtime/bin/python.exe"),
                "-X",
                "utf8",
                "-I",
                "-c",
                PROBE,
                str(output / "preflight.json"),
            ],
            cwd=output,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    if result.returncode:
        print((output / "preflight.log").read_text(encoding="utf-8", errors="replace"))
        raise SystemExit(result.returncode)
    print((output / "preflight.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
