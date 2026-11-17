"""Run the real dock on a private X server and save reviewable visual evidence.

Run with a system Python containing GTK3, Cairo and Wnck GI bindings. The only
external commands are Xvfb, a compositing window manager and dbus-run-session.
Nothing imports Docking into the observer process or patches production code.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import errno
import faulthandler
import html
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from contextlib import contextmanager, nullcontext, suppress
from pathlib import Path

from docking.platform.environment import is_freebsd, is_netbsd
from tests.visual.backends.x11.pixels import Frame, placement

ROOT = Path(__file__).resolve().parents[4]
WIDTH, HEIGHT = 1280, 800

DESKTOPS = {
    "xfce": ("xfce4-session", "XFCE", "Xfwm4"),
    "gnome": (
        "gnome-session --session=gnome --disable-acceleration-check",
        "GNOME",
        "GNOME Shell",
    ),
    "plasma": ("startplasma-x11", "KDE", "KWin"),
    "cinnamon": ("cinnamon-session", "X-Cinnamon", "Muffin"),
    "mate": ("mate-session", "MATE", "Marco"),
    "lxde": ("startlxde", "LXDE", "Openbox"),
    "lxqt": ("startlxqt", "LXQt", "Openbox"),
}
MINIMUM_CASES = (
    *(f"placement-{edge}" for edge in ("bottom", "top", "left", "right")),
    "placement-bottom-gap",
    "interaction",
    "autohide",
)


def private_environment(directory: Path, desktop: str = "xfce") -> dict[str, str]:
    """Isolate service activation as well as direct children from host settings."""
    env = dict(os.environ)
    for key in (
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "SWAYSOCK",
        "NIRI_SOCKET",
        "HYPRLAND_INSTANCE_SIGNATURE",
        "GAMESCOPE_WAYLAND_DISPLAY",
        "XDG_SESSION_DESKTOP",
        "DESKTOP_SESSION",
        "SESSION_MANAGER",
    ):
        env.pop(key, None)
    home = directory / "home"
    home.mkdir(mode=0o700)
    env["HOME"] = str(home)
    for kind in ("CONFIG", "CACHE", "DATA", "STATE", "RUNTIME"):
        path = directory / kind.lower()
        path.mkdir(mode=0o700)
        env[f"XDG_{kind}_" + ("DIR" if kind == "RUNTIME" else "HOME")] = str(path)
    env.update(
        GDK_BACKEND="x11",
        DOCKING_BACKEND="x11",
        XDG_SESSION_TYPE="x11",
        XDG_CURRENT_DESKTOP=DESKTOPS[desktop][1],
        GSETTINGS_BACKEND="memory",
        GTK_THEME="Adwaita",
        DOCKING_LOG_LEVEL="DEBUG",
        LC_ALL="C",
        GDK_SCALE="1",
        GDK_DPI_SCALE="1",
        NO_AT_BRIDGE="1",
        GIO_USE_VFS="local",
        PYTHONPATH=str(ROOT) + os.pathsep + env.get("PYTHONPATH", ""),
    )
    return env


def wait_for(check, message: str, timeout: float = 12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(message)


def stop_process_group(child: subprocess.Popen) -> None:
    """Stop owned helpers even when their launcher has already exited."""
    with suppress(ProcessLookupError):
        os.killpg(child.pid, signal.SIGTERM)
    with suppress(subprocess.TimeoutExpired):
        child.wait(timeout=5)
    with suppress(ProcessLookupError):
        os.killpg(child.pid, signal.SIGKILL)
    child.wait(timeout=5)


class SessionDirectory(tempfile.TemporaryDirectory):
    def cleanup(self):
        # Signalled desktop services can finish writing during the first removal.
        for attempt in range(10):
            try:
                super().cleanup()
                return
            except OSError as error:
                if error.errno != errno.ENOTEMPTY or attempt == 9:
                    raise
                time.sleep(0.1)


@contextmanager
def process(command: list[str], log: Path, env: dict[str, str], *, cwd: Path = ROOT):
    """Bound every child lifetime and redirect output away from the CI SSH pipe."""
    with log.open("w") as stream:
        child = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=stream,
            stderr=stream,
            start_new_session=True,
        )
        try:
            yield child
        finally:
            stop_process_group(child)


class Pointer:
    """Deliver real XTest input using each OS's native library names."""

    def __init__(self):
        self.xlib = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so")
        self.xtst = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so")
        self.xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.xlib.XOpenDisplay.restype = ctypes.c_void_p
        self.display = self.xlib.XOpenDisplay(os.environ["DISPLAY"].encode())
        if not self.display:
            raise RuntimeError("XTest could not open the private display")
        self.xlib.XFlush.argtypes = [ctypes.c_void_p]
        self.xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.xtst.XTestFakeMotionEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        self.xtst.XTestFakeButtonEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        self.xtst.XTestFakeKeyEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        self.xlib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.xlib.XKeysymToKeycode.restype = ctypes.c_uint

    def move(self, x: int, y: int):
        if not self.xtst.XTestFakeMotionEvent(self.display, -1, x, y, 0):
            raise RuntimeError("XTest motion failed")
        self.xlib.XFlush(self.display)

    def click(self, button: int = 1):
        for pressed in (1, 0):
            if not self.xtst.XTestFakeButtonEvent(self.display, button, pressed, 0):
                raise RuntimeError("XTest click failed")
        self.xlib.XFlush(self.display)

    def escape(self):
        keycode = self.xlib.XKeysymToKeycode(self.display, 0xFF1B)
        for pressed in (1, 0):
            self.xtst.XTestFakeKeyEvent(self.display, keycode, pressed, 0)
        self.xlib.XFlush(self.display)

    def close(self):
        self.xlib.XCloseDisplay(self.display)


class Observer:
    def __init__(self, output: Path, dock_command: str | None = None):
        import gi

        for namespace, version in (("Gtk", "3.0"), ("Gdk", "3.0"), ("Wnck", "3.0")):
            gi.require_version(namespace, version)
        from gi.repository import Gdk, Gio, GLib, Gtk, Wnck

        if not Gtk.init_check()[0]:
            raise RuntimeError("GTK could not open the private X server")
        self.Gdk, self.Gio, self.GLib, self.Gtk = Gdk, Gio, GLib, Gtk
        self.Wnck = Wnck
        self.handle = (
            Wnck.Handle.new(Wnck.ClientType.PAGER) if hasattr(Wnck, "Handle") else None
        )
        self.screen = (
            self.handle.get_default_screen()
            if self.handle is not None
            else Wnck.Screen.get_default()
        )
        self.output = output
        self.workarea = None
        self.dock_command = shlex.split(dock_command) if dock_command else None
        self.pointer = Pointer()
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.versions = {
            "gtk": [
                Gtk.get_major_version(),
                Gtk.get_minor_version(),
                Gtk.get_micro_version(),
            ],
            "glib": [GLib.MAJOR_VERSION, GLib.MINOR_VERSION, GLib.MICRO_VERSION],
        }

    def windows(self):
        while self.Gtk.events_pending():
            self.Gtk.main_iteration_do(False)
        self.screen.force_update()
        return self.screen.get_windows()

    def window_manager(self):
        self.windows()
        return self.screen.get_window_manager_name()

    def composited(self):
        self.windows()
        return self.Gdk.Screen.get_default().is_composited()

    def close(self):
        self.pointer.close()
        # The handle must finalize its screen before destroying its window
        # table. Release our borrowed-screen wrapper first: retaining it past
        # handle destruction leaves the screen with freed window pointers.
        self.screen = None
        self.handle = None

    def capture(self, name: str) -> Frame:
        root = self.Gdk.get_default_root_window()
        image = self.Gdk.pixbuf_get_from_window(root, 0, 0, WIDTH, HEIGHT)
        if image is None:
            raise RuntimeError("Root screenshot unavailable")
        image.savev(str(self.output / f"{name}.png"), "png", [], [])
        return Frame(
            WIDTH,
            HEIGHT,
            image.get_n_channels(),
            image.get_rowstride(),
            bytes(image.get_pixels()),
            self.workarea,
        )

    def settled(self, name: str) -> Frame:
        previous = None
        for _ in range(30):
            frame = self.capture(name)
            if previous is not None and previous.pixels == frame.pixels:
                return frame
            previous = frame
            time.sleep(0.2)
        raise AssertionError(f"Screen did not settle: {name}")

    def remote(self, method: str, signature: str = "()", arguments: tuple = ()):
        return self.bus.call_sync(
            "org.docking.Docking",
            "/org/docking/Docking",
            "org.docking.Docking.Items1",
            method,
            self.GLib.Variant(signature, arguments),
            None,
            self.Gio.DBusCallFlags.NO_AUTO_START,
            1000,
            None,
        ).unpack()

    def hover(self, app: str = "beta"):
        found, x, y, _ = self.remote("GetHoverAnchor", "(s)", (f"bsd-{app}.desktop",))
        if not found:
            raise AssertionError("Dock did not provide an input target")
        self.pointer.move(x + 24, y + 24)
        time.sleep(0.2)

    def client(self):
        return next(
            (w for w in self.windows() if w.get_name() == "BSD visual beta"), None
        )


def seed(env: dict[str, str]):
    import cairo

    applications = Path(env["XDG_DATA_HOME"]) / "applications"
    applications.mkdir(parents=True)
    icon = applications / "fixture.png"
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 64, 64)
    cr = cairo.Context(surface)
    cr.set_source_rgb(30 / 255, 190 / 255, 220 / 255)
    cr.rectangle(4, 4, 56, 56)
    cr.fill()
    surface.write_to_png(str(icon))
    for name in ("alpha", "beta", "gamma"):
        command = shlex.join(
            [sys.executable, "-m", "tests.visual.backends.x11.run", "--client"]
        )
        (applications / f"bsd-{name}.desktop").write_text(
            f"[Desktop Entry]\nType=Application\nName=BSD {name}\n"
            f"Exec={command}\nIcon={icon}\nStartupWMClass=bsd-beta\n"
        )


def fixture_client():
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    GLib.set_prgname("bsd-beta")
    window = Gtk.Window(title="BSD visual beta")
    window.set_wmclass("bsd-beta", "bsd-beta")
    window.set_default_size(420, 280)
    window.move(430, 240)
    area = Gtk.DrawingArea()

    def draw(_widget, cr):
        cr.set_source_rgb(0.75, 0.15, 0.15)
        cr.paint()

    area.connect("draw", draw)
    window.add(area)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    # Bound even clients launched by the dock if the observer crashes.
    GLib.timeout_add_seconds(120, lambda: (window.destroy(), False)[1])
    Gtk.main()


def desktop_client():
    """Provide the desktop window present in a normal Xfce session.

    Xfwm publishes its stacking property only after managing multiple clients.
    A bare WM plus one dock omits it, preventing libwnck from enumerating any
    windows. A real desktop window gives the lab the normal desktop lifecycle.
    """
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk

    window = Gtk.Window(title="BSD visual desktop")
    window.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
    window.set_decorated(False)
    window.set_accept_focus(False)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    window.stick()
    window.set_default_size(WIDTH, HEIGHT)
    window.move(0, 0)
    area = Gtk.DrawingArea()

    def draw(_widget, cr):
        cr.set_source_rgb(32 / 255, 36 / 255, 40 / 255)
        cr.paint()

    def mapped(_widget, _event):
        (Path(os.environ["XDG_RUNTIME_DIR"]) / "desktop-ready").touch()
        print("Desktop fixture mapped", flush=True)
        return False

    area.connect("draw", draw)
    window.add(area)
    window.connect("map-event", mapped)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    print("Desktop fixture shown; entering GTK event loop", flush=True)
    Gtk.main()


@contextmanager
def dock_case(observer: Observer, env: dict[str, str], name: str, overrides: dict):
    config = {
        "pinned": [f"bsd-{n}.desktop" for n in ("alpha", "beta", "gamma")],
        "icon_size": 48,
        "position": "bottom",
        "hide_mode": "none",
        "zoom_enabled": False,
        "theme": "default",
        "update_check_enabled": False,
        "startup_tips_enabled": False,
        "show_recent_apps": False,
        "global_search_enabled": False,
        "pressure_reveal_enabled": False,
        "dock_item_click_effects_enabled": False,
        **overrides,
    }
    config_path = Path(env["XDG_CONFIG_HOME"]) / "docking" / "dock.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config))
    (observer.output / f"{name}.config.json").write_text(json.dumps(config, indent=2))
    log = observer.output / f"{name}.log"
    # Let the previous dock's strut removal settle before sampling native workarea.
    observer.settled(f"{name}-desktop")
    observer.windows()
    area = observer.Gdk.Display.get_default().get_monitor(0).get_workarea()
    observer.workarea = (area.x, area.y, area.width, area.height)
    (observer.output / f"{name}.workarea.json").write_text(
        json.dumps(observer.workarea)
    )
    command = observer.dock_command or [sys.executable, "-m", "docking.launcher"]
    with process(
        command, log, env, cwd=Path("/tmp") if observer.dock_command else ROOT
    ) as child:
        yield child
    assert child.returncode == 0, f"Dock shutdown failed: {child.returncode}"
    assert "Selected session backend: x11 (" in log.read_text(errors="replace"), (
        "X11 backend not verified"
    )
    assert "Traceback (most recent call last):" not in log.read_text(
        errors="replace"
    ), f"Runtime traceback; inspect {log.name}"


def ready(observer: Observer, child, count: int = 3):
    def dock_window():
        if child.poll() is not None:
            raise AssertionError(f"Dock exited during startup: {child.returncode}")
        windows = observer.windows()
        (observer.output / "observed-windows.json").write_text(
            json.dumps(
                [
                    {
                        "pid": w.get_pid(),
                        "name": w.get_name(),
                        "type": int(w.get_window_type()),
                    }
                    for w in windows
                ],
                indent=2,
            )
        )
        return next(
            (
                w
                for w in windows
                if w.get_pid() == child.pid
                or w.get_window_type() == observer.Wnck.WindowType.DOCK
                or w.get_name() == "Docking"
            ),
            None,
        )

    window = wait_for(dock_window, "Dock window never appeared")

    # Readiness only. Subsequent assertions use window-manager state and pixels.
    def items_ready():
        try:
            return observer.remote("GetCount") == (count,)
        except observer.GLib.Error:
            return False

    wait_for(items_ready, "Dock items not ready")
    return window


def run_checks(observer: Observer, env: dict[str, str], record):
    resting_bounds = {}
    for edge, gap in (
        ("bottom", 0),
        ("top", 0),
        ("left", 0),
        ("right", 0),
        ("bottom", 16),
    ):
        name = f"placement-{edge}" + ("-gap" if gap else "")

        def check(edge=edge, gap=gap, name=name):
            observer.pointer.move(WIDTH // 2, HEIGHT // 2)
            with dock_case(
                observer,
                env,
                name,
                {"position": edge, "additional_distance_from_edge": gap},
            ) as child:
                window = ready(observer, child)
                bounds = placement(observer.settled(name), edge, gap)
                if gap and edge in resting_bounds:
                    # Compare pixels directly; GDK may publish panel workareas late.
                    assert (
                        abs(resting_bounds[edge]["bottom"] - bounds["bottom"] - gap)
                        <= 2
                    ), bounds
                elif not gap:
                    resting_bounds[edge] = bounds
                return {"pixels": bounds, "window": list(window.get_geometry())}

        record(name, check)

    def interactions():
        observer.pointer.move(WIDTH // 2, HEIGHT // 2)
        with dock_case(observer, env, "interaction", {}) as child:
            ready(observer, child)
            idle = observer.settled("interaction-idle")
            bounds = placement(idle, "bottom")
            observer.hover()
            wait_for(
                lambda: (
                    idle.changed_above(
                        observer.capture("interaction-tooltip"), bounds["top"] - 8
                    )
                    > 80
                ),
                "Tooltip pixels absent",
            )
            tooltip = observer.settled("interaction-tooltip")
            assert idle.changed_above(tooltip, bounds["top"] - 8) > 80, (
                "Tooltip pixels absent"
            )
            observer.pointer.click(3)
            wait_for(
                lambda: (
                    idle.changed_above(
                        observer.capture("interaction-menu"), bounds["top"] - 8
                    )
                    > 500
                ),
                "Menu pixels absent",
            )
            menu = observer.settled("interaction-menu")
            assert idle.changed_above(menu, bounds["top"] - 8) > 500, (
                "Menu pixels absent"
            )
            observer.pointer.escape()
            observer.hover()
            observer.pointer.click()
            wait_for(observer.client, "Click did not launch the GTK fixture")
            wait_for(
                lambda: observer.client().is_active(), "Launched client never focused"
            )
            observer.pointer.move(WIDTH // 2, HEIGHT // 2)
            observer.settled("interaction-launched")
            assert observer.remote("GetCount") == (3,), (
                "Running app duplicated its pinned icon"
            )
            for minimized, phase in ((True, "minimized"), (False, "restored")):
                observer.hover()
                observer.pointer.click()
                wait_for(
                    lambda minimized=minimized: (
                        observer.client().is_minimized() == minimized
                    ),
                    f"Click did not {phase}",
                )
                observer.pointer.move(WIDTH // 2, HEIGHT // 2)
                observer.settled(f"interaction-{phase}")
            # Release the GI wrapper before libwnck processes DestroyNotify;
            # retaining a closed WnckWindow can crash older libwnck versions.
            observer.client().close(0)
            wait_for(lambda: observer.client() is None, "Fixture did not close")
            return {"launch": True, "minimize": True, "restore": True}

    record("interaction", interactions)

    def zoom():
        observer.pointer.move(WIDTH // 2, HEIGHT // 2)
        with dock_case(
            observer, env, "zoom", {"zoom_enabled": True, "tooltips_enabled": False}
        ) as child:
            ready(observer, child)
            idle = placement(observer.settled("zoom-idle"), "bottom")
            observer.hover()
            time.sleep(0.5)
            hovered = observer.settled("zoom-hovered").marker()
            assert hovered and hovered["count"] > idle["count"] * 1.15, (idle, hovered)
            return {"idle": idle, "hovered": hovered}

    record("zoom", zoom)

    for edge in ("bottom", "top", "left", "right"):
        name = f"reservation-{edge}"

        def reservation(edge=edge, name=name):
            observer.pointer.move(WIDTH // 2, HEIGHT // 2)
            with process(
                [sys.executable, "-m", "tests.visual.backends.x11.run", "--client"],
                observer.output / f"{name}.client.log",
                env,
            ):
                client = wait_for(observer.client, "Reservation fixture absent")
                with dock_case(observer, env, name, {"position": edge}) as child:
                    ready(observer, child)
                    client.maximize()
                    wait_for(
                        lambda: observer.client().is_maximized(),
                        "Fixture did not maximize",
                    )
                    time.sleep(0.4)
                    bounds = placement(observer.settled(name), edge)
                    # Capturing pixels does not dispatch the observer's Wnck
                    # events. Refresh its cached geometry after the WM settles.
                    observer.windows()
                    x, y, width, height = client.get_geometry()
                    overlaps = (
                        x <= bounds["right"]
                        and x + width > bounds["left"]
                        and y <= bounds["bottom"]
                        and y + height > bounds["top"]
                    )
                    assert not overlaps, (
                        f"Maximized client {(x, y, width, height)} overlaps "
                        f"reserved dock area {bounds}"
                    )
                    reserved = [x, y, width, height]

                def full_workarea():
                    observer.windows()
                    x, y, width, height = client.get_geometry()
                    return (
                        x == 0
                        and y == 0
                        and width >= WIDTH - 2
                        and height >= HEIGHT - 2
                    )

                wait_for(full_workarea, "Workarea did not recover after dock exit")
                observer.settled(f"{name}-released")
                return {
                    "reserved_client_frame": reserved,
                    "released_client_frame": list(client.get_geometry()),
                }

        record(name, reservation)

    def autohide():
        observer.pointer.move(WIDTH // 2, HEIGHT // 2)
        with dock_case(
            observer,
            env,
            "autohide",
            {"hide_mode": "autohide", "tooltips_enabled": False},
        ) as child:
            ready(observer, child)
            # Establish a real enter/leave transition before checking the policy.
            observer.hover()
            observer.pointer.move(WIDTH // 2, HEIGHT // 2)
            time.sleep(1)
            assert observer.settled("autohide-hidden").marker() is None, (
                "Dock did not hide"
            )
            x, y, width, height = observer.workarea
            observer.pointer.move(x + width // 2, y + height - 1)
            time.sleep(0.7)
            revealed = observer.settled("autohide-revealed").marker()
            assert revealed and revealed["count"] > 900, (
                "Edge motion did not reveal dock"
            )
            observer.pointer.move(WIDTH // 2, HEIGHT // 2)
            time.sleep(0.7)
            assert observer.settled("autohide-hidden-again").marker() is None, (
                "Dock did not hide again"
            )
            return {"revealed": revealed}

    record("autohide", autohide)

    def applets():
        observer.pointer.move(WIDTH // 2, HEIGHT // 2)
        pinned = [
            f"applet://{name}"
            for name in (
                "calculator",
                "systemmonitor",
                "network",
                "battery",
                "clock",
                "volume",
                "pet",
                "thermals",
                "camshield",
            )
        ]
        if is_netbsd():
            pinned.remove("applet://camshield")
        if sys.platform.startswith("linux") or is_freebsd():
            pinned.append("applet://bluetooth")
        with dock_case(observer, env, "applets", {"pinned": pinned}) as child:
            ready(observer, child, count=len(pinned))
            time.sleep(2.5)
            observer.capture("applets-live")
            found, x, y, _ = observer.remote(
                "GetHoverAnchor", "(s)", ("applet://network",)
            )
            assert found, "Native Network applet did not load"
            observer.pointer.move(x + 24, y + 24)
            observer.pointer.click(3)
            time.sleep(0.5)
            observer.capture("applets-network-menu")
            observer.pointer.escape()
            for applet_id in ("camshield", "bluetooth"):
                if f"applet://{applet_id}" not in pinned:
                    continue
                found, x, y, _ = observer.remote(
                    "GetHoverAnchor", "(s)", (f"applet://{applet_id}",)
                )
                assert found, f"{applet_id} applet did not load"
                observer.pointer.move(x + 24, y + 24)
                observer.pointer.click(3)
                time.sleep(0.5)
                observer.capture(f"applets-{applet_id}-menu")
                observer.pointer.escape()
            return {"pinned": pinned}

    record("applets", applets)


def report(output: Path, metadata: dict, results: list[dict]):
    payload = {"environment": metadata, "results": results}
    (output / "results.json").write_text(json.dumps(payload, indent=2))
    rows = "".join(
        f"<tr><td>{html.escape(r['case'])}</td><td>{r['status']}</td>"
        f"<td><pre>{html.escape(str(r.get('detail', '')))}</pre></td></tr>"
        for r in results
    )
    images = "".join(
        f'<figure><a href="{p.name}"><img src="{p.name}" loading="lazy"></a>'
        f"<figcaption>{html.escape(p.name)}</figcaption></figure>"
        for p in sorted(output.glob("*.png"))
    )
    logs = " ".join(
        f'<a href="{p.name}">{p.name}</a>' for p in sorted(output.glob("*.log"))
    )
    (output / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Docking X11 visual checks</title>"
        "<style>body{font:16px system-ui;margin:2em;background:#202428;color:#eee}"
        "a{color:#7dd3fc}img{max-width:100%}figure{margin:2em 0}"
        "td{padding:.5em;border:1px solid #777}pre{white-space:pre-wrap}</style>"
        "<h1>Docking X11 visual checks</h1><pre>"
        + html.escape(json.dumps(metadata, indent=2))
        + f"</pre><table>{rows}</table><p>{logs}</p>{images}"
    )


def environment_metadata(args):
    metadata = {"os": platform.platform(), "python": sys.version, "wm": args.wm}
    metadata["commit"] = os.environ.get("GITHUB_SHA", "unknown")
    if shutil.which("git"):
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if revision.returncode == 0:
            metadata["commit"] = revision.stdout.strip()
    return metadata


def observe(args):
    """The GTK observer exits while its controller still owns a live X server."""
    output = args.output.resolve()
    metadata = environment_metadata(args)
    results = []
    observer = None

    def record(name, check):
        if args.case and name not in args.case:
            return
        try:
            results.append({"case": name, "status": "pass", "detail": check()})
        except Exception:
            results.append(
                {"case": name, "status": "fail", "detail": traceback.format_exc()}
            )
            if observer is not None:
                try:
                    observer.capture(f"{name}-failure")
                except Exception:
                    results[-1]["capture_error"] = traceback.format_exc()
        report(output, metadata, results)
        print(f"{name}: {results[-1]['status']}", flush=True)

    try:
        observer = Observer(output, args.dock_command)
        metadata.update(observer.versions)
        wait_for(observer.window_manager, "Window manager never became ready")
        metadata["desktop"] = args.desktop
        metadata["window_manager"] = observer.window_manager()
        if args.minimum:
            assert (
                DESKTOPS[args.desktop][2].casefold()
                in metadata["window_manager"].casefold()
            ), "Unexpected native desktop window manager"
        wait_for(
            observer.composited,
            "Window manager compositing unavailable",
        )
        if args.minimum:

            def panel_ready():
                observer.windows()
                monitor = observer.Gdk.Display.get_default().get_monitor(0)
                area, geometry = monitor.get_workarea(), monitor.get_geometry()
                return (area.x, area.y, area.width, area.height) != (
                    geometry.x,
                    geometry.y,
                    geometry.width,
                    geometry.height,
                )

            wait_for(
                panel_ready, "Desktop panel workarea never became ready", timeout=30
            )

            def desktop_painted():
                frame = observer.capture("desktop-ready")
                colored = 0
                for y in range(frame.height):
                    for x in range(frame.width):
                        offset = y * frame.stride + x * frame.channels
                        if any(frame.pixels[offset : offset + 3]):
                            colored += 1
                            if colored > 1000:
                                return True
                return False

            wait_for(desktop_painted, "Native desktop never painted", timeout=30)
            if args.desktop == "gnome":
                observer.pointer.escape()
                time.sleep(0.5)
                observer.pointer.escape()
        if args.pytest:

            def unit_tests():
                with process(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "-q",
                        "tests/core",
                        "tests/platform",
                        *(
                            str(path.relative_to(ROOT))
                            for path in sorted(
                                (ROOT / "tests/applets").glob("test_bsd_*.py")
                            )
                        ),
                        "tests/visual/backends/x11/test_harness.py",
                        "tests/test_bsd_packaging.py",
                        "tests/test_bsd_release.py",
                        "tests/applets/test_battery.py",
                        "tests/applets/test_volume.py",
                        "tests/applets/test_hardware_requests.py",
                        f"--junitxml={output / 'unit-tests.xml'}",
                    ],
                    output / "unit-tests.log",
                    dict(os.environ),
                ) as child:
                    assert child.wait(timeout=300) == 0, (
                        "Unit tests failed; inspect unit-tests.log"
                    )
                return {"junit": "unit-tests.xml"}

            record("unit-tests", unit_tests)
        run_checks(observer, dict(os.environ), record)
    except Exception:
        failure = {
            "case": "observer-setup",
            "status": "fail",
            "detail": traceback.format_exc(),
        }
        if args.desktop == "lxde":
            with (output / "mapping-state.log").open("w") as stream:
                for command in (
                    ["xprop", "-root", "_NET_CURRENT_DESKTOP", "_NET_WORKAREA", "_NET_CLIENT_LIST", "_NET_SUPPORTED"],
                    ["xwininfo", "-root", "-tree"],
                ):
                    subprocess.run(command, stdout=stream, stderr=stream, check=False)
                tree = subprocess.run(["xwininfo", "-root", "-tree"], capture_output=True, text=True, check=False)
                for xid in re.findall(r'^\s*(0x[0-9a-fA-F]+) "(?:panel|Desktop|Error)"', tree.stdout, re.MULTILINE):
                    subprocess.run(["xwininfo", "-id", xid, "-all"], stdout=stream, stderr=stream, check=False)
                    subprocess.run(["xprop", "-id", xid], stdout=stream, stderr=stream, check=False)
                for app in ("openbox", "picom", "Xvfb"):
                    matches = subprocess.run(["pgrep", "-x", app], capture_output=True, text=True, check=False)
                    for pid in matches.stdout.split():
                        stream.write(f"\nProcess {app} {pid}\n")
                        stream.flush()
                        try:
                            subprocess.run(["gdb", "--batch", "-ex", "set debuginfod enabled off", "-ex", "thread apply all bt", "-p", pid], stdout=stream, stderr=stream, check=False, timeout=12)
                        except subprocess.TimeoutExpired:
                            stream.write("GDB inspection timed out\n")
                for app in ("lxpanel", "pcmanfm", "lxpolkit"):
                    matches = subprocess.run(["pgrep", "-x", app], capture_output=True, text=True, check=False)
                    for pid in matches.stdout.split():
                        stream.write(f"\nProcess {app} {pid}\n")
                        stream.flush()
                        try:
                            subprocess.run(["gdb", "--batch", "-x", str(ROOT / "tests/visual/backends/x11/lxde/mapping.gdb"), "-p", pid], stdout=stream, stderr=stream, check=False, timeout=12)
                        except subprocess.TimeoutExpired:
                            stream.write("GDB inspection timed out\n")
        if observer is not None:
            try:
                observer.capture("observer-setup-failure")
            except Exception:
                failure["capture_error"] = traceback.format_exc()
        results.append(failure)
    finally:
        if observer is not None:
            observer.close()
    report(output, metadata, results)
    return int(not results or any(r["status"] != "pass" for r in results))


def session(args):
    """Own test services without loading GTK into the X-server controller."""
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    metadata = environment_metadata(args)
    results = []
    try:
        # The bus controller removes runtime directories after its services stop.
        env = dict(os.environ)
        if args.minimum:
            env["GSETTINGS_BACKEND"] = "keyfile"
            if args.desktop == "gnome":
                env["DBUS_SYSTEM_BUS_ADDRESS"] = env["DBUS_SESSION_BUS_ADDRESS"]
            if args.desktop == "plasma":
                env["KWIN_COMPOSE"] = "O2"
                env["QT_QUICK_BACKEND"] = "software"
            autostart = Path(env["XDG_CONFIG_HOME"]) / "autostart"
            autostart.mkdir()
            (autostart / "picom.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Picom\nHidden=true\n"
            )
            if args.desktop == "mate":
                subprocess.run(
                    [
                        "gsettings",
                        "set",
                        "org.mate.Marco.general",
                        "compositing-manager",
                        "true",
                    ],
                    env=env,
                    check=True,
                )
        seed(env)
        display_number = next(
            n
            for n in range(90, 190)
            if not Path(f"/tmp/.X{n}-lock").exists()
            and not Path(f"/tmp/.X11-unix/X{n}").exists()
        )
        env["DISPLAY"] = f":{display_number}"
        with process(
            [
                "Xvfb",
                env["DISPLAY"],
                "-screen",
                "0",
                f"{WIDTH}x{HEIGHT}x24",
                "-nolisten",
                "tcp",
                "-ac",
                "-noreset",
            ],
            output / "xvfb.log",
            env,
        ) as xvfb:

            def display_ready():
                if xvfb.poll() is not None:
                    raise RuntimeError("Private Xvfb exited; inspect xvfb.log")
                return Path(f"/tmp/.X11-unix/X{display_number}").exists()

            wait_for(display_ready, "Private Xvfb never became ready")
            with (
                process(shlex.split(args.wm), output / "wm.log", env),
                (
                    process(
                        ["picom", "--backend", "xrender", "--config", "/dev/null"],
                        output / "picom.log",
                        env,
                    )
                    if args.desktop in {"lxde", "lxqt"}
                    else nullcontext()
                ),
                (
                    process(
                        [
                            sys.executable,
                            "-m",
                            "tests.visual.backends.x11.run",
                            "--background",
                        ],
                        output / "desktop.log",
                        env,
                    )
                    if not args.minimum
                    else nullcontext()
                ) as background,
            ):

                def desktop_ready():
                    if background.poll() is not None:
                        raise RuntimeError(
                            "Desktop fixture exited; inspect desktop.log"
                        )
                    return (Path(env["XDG_RUNTIME_DIR"]) / "desktop-ready").exists()

                if not args.minimum:
                    wait_for(desktop_ready, "Desktop fixture never mapped", timeout=30)
                with process(
                    [
                        sys.executable,
                        "-m",
                        "tests.visual.backends.x11.run",
                        "--observe",
                        "--output",
                        str(output),
                        "--wm",
                        args.wm,
                        "--desktop",
                        args.desktop,
                        *(["--minimum"] if args.minimum else []),
                        *(["--pytest"] if args.pytest else []),
                        *(
                            ["--dock-command", args.dock_command]
                            if args.dock_command
                            else []
                        ),
                        *[
                            argument
                            for name in args.case
                            for argument in ("--case", name)
                        ],
                    ],
                    output / "observer.log",
                    env,
                ) as child:
                    status = child.wait(timeout=900)
                payload_path = output / "results.json"
                if payload_path.exists():
                    payload = json.loads(payload_path.read_text())
                    metadata, results = payload["environment"], payload["results"]
                if status:
                    results.append(
                        {
                            "case": "observer-exit",
                            "status": "fail",
                            "detail": f"Observer exit {status}; see observer.log",
                        }
                    )
        for log in output.glob("*.log"):
            if "Traceback (most recent call last):" in log.read_text(errors="replace"):
                results.append(
                    {
                        "case": log.name,
                        "status": "fail",
                        "detail": "Python traceback in runtime log",
                    }
                )
    except Exception:
        results.append(
            {"case": "setup", "status": "fail", "detail": traceback.format_exc()}
        )
    if args.desktop == "lxde":
        with suppress(FileNotFoundError):
            shutil.copyfile(
                Path(env["XDG_CACHE_HOME"]) / "lxsession/LXDE/run.log",
                output / "lxsession.log",
            )
    report(output, metadata, results)
    for result in results:
        print(f"{result['case']}: {result['status']}", flush=True)
    return int(not results or any(r["status"] != "pass" for r in results))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "bsd-visual" / str(time.time_ns()),
    )
    parser.add_argument("--wm")
    parser.add_argument("--desktop", choices=DESKTOPS, default="xfce")
    parser.add_argument("--minimum", action="store_true")
    parser.add_argument(
        "--pytest",
        action="store_true",
        help="Run core/platform and native applet tests in the private desktop.",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="Run only a named case (repeatable).",
    )
    parser.add_argument("--observe", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dock-command", help="Exercise an installed Docking command.")
    parser.add_argument("--session", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--client", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.wm is None:
        args.wm = (
            DESKTOPS[args.desktop][0]
            if args.minimum
            else "xfwm4 --compositor=on --sm-client-disable --vblank=off"
        )
    if args.minimum and not args.case:
        args.case = list(MINIMUM_CASES)
    if args.client:
        fixture_client()
        return 0
    if args.background:
        desktop_client()
        return 0
    if args.observe:
        faulthandler.enable()
        return observe(args)
    if args.session:
        faulthandler.enable()
        return session(args)
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be empty; use a new directory for each run")
    # Exclude the host's D-Bus activation directories: desktop portals, keyrings
    # and accessibility daemons belong to the host session, not this fixture.
    with SessionDirectory(prefix="docking-test-bus-") as directory:
        bus_env = private_environment(Path(directory), args.desktop)
        service_dir = Path(directory) / "services"
        service_dir.mkdir()
        # Xfwm needs its settings daemon. Permit this one service without
        # activating the host's portals, keyring, or session services.
        for prefix in ("/usr/pkg/share", "/usr/local/share", "/usr/share"):
            source = Path(prefix) / "dbus-1/services/org.xfce.Xfconf.service"
            if source.exists():
                shutil.copyfile(source, service_dir / source.name)
                break
        if args.minimum:
            for prefix in ("/usr/pkg/share", "/usr/local/share", "/usr/share"):
                for source in (Path(prefix) / "dbus-1/services").glob("*.service"):
                    if source.name.startswith(
                        (
                            "org.kde.",
                            "org.gnome.",
                            "org.mate.",
                            "org.lxde.",
                            "org.lxqt.",
                        )
                    ):
                        shutil.copyfile(source, service_dir / source.name)
        bus_config = Path(directory) / "session.conf"
        bus_config.write_text(
            "<busconfig><type>session</type><listen>unix:tmpdir=/tmp</listen>"
            f"<servicedir>{html.escape(str(service_dir))}</servicedir>"
            '<auth>EXTERNAL</auth><policy context="default">'
            '<allow send_destination="*"/><allow receive_sender="*"/>'
            '<allow own="*"/></policy></busconfig>'
        )
        with subprocess.Popen(
            [
                "dbus-run-session",
                f"--config-file={bus_config}",
                "--",
                sys.executable,
                "-m",
                "tests.visual.backends.x11.run",
                "--session",
                "--output",
                str(args.output),
                "--wm",
                args.wm,
                "--desktop",
                args.desktop,
                *(["--minimum"] if args.minimum else []),
                *(["--pytest"] if args.pytest else []),
                *(["--dock-command", args.dock_command] if args.dock_command else []),
                *[argument for name in args.case for argument in ("--case", name)],
            ],
            cwd=ROOT,
            env=bus_env,
            start_new_session=True,
        ) as bus:
            try:
                status = bus.wait()
            finally:
                stop_process_group(bus)
        print(
            f"Visual report: {args.output / 'index.html'} (session exit {status})",
            flush=True,
        )
        results_path = args.output / "results.json"
        if status and results_path.exists():
            payload = json.loads(results_path.read_text())
            if all(result["status"] == "pass" for result in payload["results"]):
                payload["results"].append(
                    {
                        "case": "session-exit",
                        "status": "fail",
                        "detail": f"Observer exited with status {status}",
                    }
                )
                report(args.output, payload["environment"], payload["results"])
        return status


if __name__ == "__main__":
    sys.exit(main())
