"""In-container probe for the Cinnamon/Muffin lab lane.

Cinnamon is the one pilot compositor that can report the dock's *real* rectangle.
`org.Cinnamon.Eval` runs JavaScript inside the shell, so the probe can reach
`global.get_window_actors()` and read `get_frame_rect()` directly. That makes
Cinnamon the reference lane for geometry: everywhere else the harness measures
from pixels, here it can ask.

This matters because Docking's own reporting is not trustworthy for the purpose:
`WaylandLayerShellSurfaceService.get_surface_position()` returns the *requested*
position (docking/platform/backends/wayland/services.py:110-114), which is the
very thing under test.

Usage:
    cinnamon_probe.py geometry
    cinnamon_probe.py capabilities
    cinnamon_probe.py screenshot <absolute-path.png>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

BUS_NAME = "org.Cinnamon"
OBJECT_PATH = "/org/Cinnamon"
INTERFACE = "org.Cinnamon"

# Docking's dock window. Older/other paths do not set a title token, so match on
# a prefix and fall back to the WM class rather than assuming one shape.
DOCK_TITLE_PREFIX = "Docking"


def _proxy() -> Gio.DBusProxy:
    return Gio.DBusProxy.new_for_bus_sync(
        Gio.BusType.SESSION,
        Gio.DBusProxyFlags.DO_NOT_AUTO_START,
        None,
        BUS_NAME,
        OBJECT_PATH,
        INTERFACE,
        None,
    )


def _eval(proxy: Gio.DBusProxy, script: str) -> object:
    success, value = proxy.call_sync(
        "Eval",
        GLib.Variant("(s)", (script,)),
        Gio.DBusCallFlags.NO_AUTO_START,
        5000,
        None,
    ).unpack()
    if not success:
        raise RuntimeError(f"Cinnamon Eval failed: {value}")
    return json.loads(value)


MONITORS_SCRIPT = """
(() => {
    const ws = global.workspace_manager.get_active_workspace();
    const out = [];
    for (let i = 0; i < global.display.get_n_monitors(); i++) {
        const g = global.display.get_monitor_geometry(i);
        const a = ws.get_work_area_for_monitor(i);
        out.push({
            name: "Cinnamon-" + i,
            x: g.x, y: g.y, width: g.width, height: g.height,
            workarea: {x: a.x, y: a.y, width: a.width, height: a.height},
            scale: 1
        });
    }
    return out;
})()
"""

WINDOWS_SCRIPT = """
global.get_window_actors().map(actor => {
    const w = actor.meta_window;
    const r = w.get_frame_rect();
    return {
        title: String(w.get_title() || ""),
        wm_class: String(w.get_wm_class() || ""),
        pid: w.get_pid(),
        maximized: !!w.maximized_horizontally && !!w.maximized_vertically,
        x: r.x, y: r.y, width: r.width, height: r.height
    };
})
"""


def _find_dock(windows: list[dict]) -> dict | None:
    for window in windows:
        if window["title"].startswith(DOCK_TITLE_PREFIX):
            return window
    for window in windows:
        if "docking" in window["wm_class"].lower():
            return window
    return None


def geometry() -> int:
    proxy = _proxy()
    monitors = _eval(proxy, MONITORS_SCRIPT)
    windows = _eval(proxy, WINDOWS_SCRIPT)
    dock = _find_dock(windows)
    json.dump(
        {
            "outputs": monitors,
            "windows": windows,
            "dock_rect": (
                {k: dock[k] for k in ("x", "y", "width", "height")} if dock else None
            ),
            "dock_title": dock["title"] if dock else None,
        },
        sys.stdout,
        indent=2,
    )
    print()
    return 0


def _muffin_debug_available() -> bool:
    """Whether Muffin's read-only window snapshot API answers.

    `CinnamonWaylandSessionBackend` needs this *as well as* layer-shell
    (docking/platform/backends/selection.py:_create_cinnamon_wayland_backend),
    so probing only layer-shell would over-predict the backend.
    """
    try:
        proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.DO_NOT_AUTO_START,
            None,
            "org.cinnamon.Muffin.Debug",
            "/org/cinnamon/Muffin/Debug",
            "org.cinnamon.Muffin.Debug",
            None,
        )
        proxy.call_sync(
            "ListWindows", None, Gio.DBusCallFlags.NO_AUTO_START, 2000, None
        )
        return True
    except Exception:
        return False


def capabilities() -> int:
    """Report the backend Cinnamon's conditions actually imply.

    Probed rather than hard-coded, because the answer decides which code path
    runs and it varies by release: Muffin gained layer-shell in 6.7, and this
    image ships 6.6.x. A hard-coded expectation would be wrong on one of them.

    Older Muffin releases use Docking's Cinnamon shell bridge for positioning
    and reservation. Probe that bridge too, so the #347 regression actually runs
    instead of being reported unsupported.
    """
    import gi as _gi

    _gi.require_version("Gtk", "3.0")
    _gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk

    _gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell

    Gtk.init([])
    display = Gdk.Display.get_default()
    wayland = type(display).__name__ == "GdkWaylandDisplay"
    layer_shell = bool(GtkLayerShell.is_supported())
    native = layer_shell and _muffin_debug_available()

    from docking.platform.backends.cinnamon.shell import CinnamonShellClient

    shell = CinnamonShellClient.connect() is not None
    reasons: dict[str, str] = {}
    if not native and not shell:
        reasons["placement"] = (
            "Muffin here does not implement layer-shell, so Docking falls back "
            "to the reduced tier where window.move() is ignored -- this is "
            "issue #347, fixed by PR #349"
        )

    json.dump(
        {
            "compositor": "cinnamon",
            "expected_backend": "cinnamon-wayland"
            if native
            else "cinnamon-shell"
            if shell
            else "reduced",
            "native_geometry": True,
            "pointer": False,
            "placement": native or shell,
            "window_actions": shell,
            "reservation_probe": True,
            "unsupported_reasons": reasons,
            "screenshot_method": "org.Cinnamon.Screenshot",
            "gtk_display_is_wayland": wayland,
            "layer_shell_supported": layer_shell,
            "muffin_debug_available": _muffin_debug_available(),
        },
        sys.stdout,
        indent=2,
    )
    print()
    return 0


def maximized_window() -> int:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    window = Gtk.Window(title="Lab maximized window")
    window.set_default_size(500, 300)
    window.connect("destroy", Gtk.main_quit)
    window.add(Gtk.Label(label="Maximized Wayland reservation probe"))
    window.maximize()
    window.show_all()
    Gtk.main()
    return 0


def reservation() -> int:
    """Read an actual maximized window frame and workarea from Muffin."""
    proxy = _proxy()
    previous = None
    for _ in range(60):
        windows = _eval(proxy, WINDOWS_SCRIPT)
        window = next(
            (w for w in windows if w["title"] == "Lab maximized window"), None
        )
        outputs = _eval(proxy, MONITORS_SCRIPT)
        state = {"window": window, "outputs": outputs}
        if window and window["maximized"] and state == previous:
            json.dump(state, sys.stdout, indent=2)
            print()
            return 0
        previous = state
        time.sleep(0.1)
    raise RuntimeError("maximized test window never settled")


def screenshot(dest: str) -> int:
    """Capture the nested desktop via Cinnamon's own screenshot API.

    The D-Bus call can return before the PNG has been written
    (docs/HEADLESS_WAYLAND_TESTING.local.md:400-407), so poll for a non-empty
    file rather than trusting the reply.
    """
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)

    proxy = _proxy()
    _eval(proxy, "global.stage.hide_cursor(); true")
    proxy.call_sync(
        "Screenshot",
        GLib.Variant("(bbs)", (False, False, str(path))),
        Gio.DBusCallFlags.NO_AUTO_START,
        10000,
        None,
    )
    for _ in range(60):
        if path.exists() and path.stat().st_size > 0:
            return 0
        time.sleep(0.1)
    print(f"no screenshot produced at {path}", file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    mode = sys.argv[1]
    if mode == "geometry":
        return geometry()
    if mode == "capabilities":
        return capabilities()
    if mode == "maximized-window":
        return maximized_window()
    if mode == "reservation":
        return reservation()
    if mode == "screenshot" and len(sys.argv) > 2:
        return screenshot(sys.argv[2])
    print(f"unknown mode: {mode}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
