"""Native Cinnamon service regressions using real GTK dock input and pixels."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

# The UI probe runs the application in this process; choose its transport
# before importing Gtk, exactly as the production entry point does.
if "--child" not in sys.argv:
    from docking import app

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from cinnamon_probe import _eval, _proxy, screenshot
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
from input_probe import X11Pointer


def child(name):
    GLib.set_prgname(f"lab-{name}")
    window = Gtk.Window(title=f"Native services {name}")
    window.set_default_size(400, 300)
    area = Gtk.DrawingArea()

    def draw(_widget, cr):
        cr.set_source_rgb(1 if name == "alpha" else 0, 0, 1 if name != "alpha" else 0)
        cr.paint()
        return False

    area.connect("draw", draw)
    window.add(area)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()


class Probe:
    def __init__(self, items, model, backend):
        self.window = items._window
        self.model, self.backend = model, backend
        self.shell = _proxy()
        self.evidence = Path(os.environ["LAB_DIR"])
        self.pointer = X11Pointer(
            (self.evidence / "outer-display").read_text().strip(),
            (self.evidence / "outer-authority").read_text().strip(),
        )
        self.observations = {}
        self.children = []

    def settle(self, seconds=0.4):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            time.sleep(0.01)

    def wait(self, predicate):
        end = time.monotonic() + 6
        while time.monotonic() < end:
            if predicate():
                return
            self.settle(0.1)
        raise AssertionError(f"Condition did not converge; state: {self.state()}")

    def state(self):
        return _eval(
            self.shell,
            """
            global.get_window_actors().map(a=>a.meta_window)
            .filter(w=>w && w.get_workspace() &&
                w.get_title()?.startsWith('Native services '))
            .map(w=>{const r=w.get_frame_rect(); return {
                title:w.get_title(), id:w.get_stable_sequence(), pid:w.get_client_pid(),
                rect:[r.x,r.y,r.width,r.height], minimized:!!w.minimized,
                visible:w.showing_on_its_workspace(),
                workspace:w.get_workspace().index(),
                focused:w===global.display.focus_window, client_type:w.get_client_type()
            };})
        """,
        )

    def row(self, name):
        return next(w for w in self.state() if w["title"] == f"Native services {name}")

    def act(self, name, code):
        assert (
            _eval(
                self.shell,
                "(() => {const w=global.get_window_actors().map(a=>a.meta_window)"
                ".find(w=>w && w.get_workspace() && w.get_title()==="
                f"{json.dumps('Native services ' + name)});"
                "if (!w) return false;" + code + ";return true;})()",
            )
            is True
        )
        self.settle()

    def motion(self, x, y):
        self.pointer.motion_absolute(0, x, y, 1280, 720)
        self.pointer.frame()

    def click(self, x, y):
        self.motion(x, y)
        self.settle(0.2)
        self.pointer.button(0, 272, 1)
        self.pointer.frame()
        # A modal applet can enter a nested GTK loop in its press handler.
        # Deliver the release independently of that handler, as a real mouse
        # does, rather than holding the button until the selection completes.
        self.pointer.button(0, 272, 0)
        self.pointer.frame()
        self.settle()

    def escape(self):
        keysym = self.pointer.xlib.XKeysymToKeycode
        keysym.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        keysym.restype = ctypes.c_uint
        key = keysym(self.pointer.display, 0xFF1B)
        fake_key = self.pointer.xtst.XTestFakeKeyEvent
        fake_key.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        for pressed in (1, 0):
            fake_key(self.pointer.display, key, pressed, 0)
            self.pointer.frame()
        self.settle()

    def click_item(self, desktop_id):
        self.backend.windows.refresh()
        anchor = self.window.get_hover_anchor(desktop_id=desktop_id)
        assert anchor, desktop_id
        self.click(anchor[0] + 24, anchor[1] + 24)

    def later(self, action):
        deadline = time.monotonic() + 6

        def run():
            ready = _eval(
                self.shell,
                "Object.values(global._dockingPicks || {}).some(s=>!s.done)",
            )
            if not ready:
                assert time.monotonic() < deadline, "Compositor selection did not start"
                return True
            action()
            return False

        GLib.timeout_add(50, run)

    def capture(self, name):
        path = self.evidence / f"services-{name}.png"
        screenshot(str(path))
        deadline = time.monotonic() + 2
        while True:
            try:
                return GdkPixbuf.Pixbuf.new_from_file(str(path))
            except GLib.Error:
                if time.monotonic() >= deadline:
                    raise
                self.settle(0.05)

    def spawn(self, name, *, xwayland=False):
        environment = dict(os.environ)
        environment["GDK_BACKEND"] = "wayland"
        if xwayland:
            environment["GDK_BACKEND"] = "x11"
            environment["DISPLAY"] = _eval(
                self.shell, "imports.gi.GLib.getenv('DISPLAY')"
            )
            environment["XAUTHORITY"] = _eval(
                self.shell, "imports.gi.GLib.getenv('XAUTHORITY')"
            )
        process = subprocess.Popen(
            [sys.executable, __file__, "--child", name], env=environment
        )
        self.children.append(process)
        self.wait(
            lambda: any(w["title"] == f"Native services {name}" for w in self.state())
        )
        return process

    def run(self):
        display = type(Gdk.Display.get_default()).__name__
        if self.backend.name == "cinnamon-xwayland":
            assert display == "X11Display", display
            assert not self.backend.surface.popups_use_parent_relative_coordinates
        else:
            assert display == "GdkWaylandDisplay", display
        self.dock_role()
        assert (
            self.backend.workspaces
            and self.backend.desktop_actions
            and self.backend.idle
        )
        assert self.backend.screen_capture and self.backend.window_picker
        self.observations["backend"] = self.backend.name
        self.observations["features"] = sorted(self.backend.windows.shell.features)
        alpha = self.spawn("alpha")
        beta = self.spawn("beta")
        self.act("alpha", "w.move_resize_frame(true, 100, 100, 400, 300)")
        self.act("beta", "w.move_resize_frame(true, 560, 100, 400, 300)")
        self.workspaces()
        self.previews()
        self.desktop()
        self.idle()
        self.colors()
        self.dodge()
        self.picking(alpha, beta)
        self.client_cleanup()
        self.capture("finished")

    def dock_role(self):
        """Check the compositor and real Cinnamon UI models, not GTK hints."""
        state = _eval(
            self.shell,
            """
            (() => {
                const Meta=imports.gi.Meta, Main=imports.ui.main;
                const tracker=imports.gi.Cinnamon.WindowTracker.get_default();
                const w=global.get_window_actors().map(a=>a.meta_window)
                    .find(w=>w && w.get_workspace() &&
                        (w.get_client_pid?.() || w.get_pid())===__PID__);
                if (!w) return null;
                const panel=Main.AppletManager.getRunningInstancesForUuid(
                    'grouped-window-list@cinnamon.org');
                const switcher=imports.ui.appSwitcher.appSwitcher.getWindowsForBinding(
                    {get_name:()=> 'switch-windows'});
                return {type:w.get_window_type(), dock:Meta.WindowType.DOCK,
                    skip_taskbar:w.is_skip_taskbar(),
                    interesting:tracker.is_window_interesting(w),
                    application:tracker.get_window_app(w)?.get_id() || null,
                    switcher:switcher.includes(w),
                    panel_instances:panel.length,
                    panel:panel.some(a=>a.workspaces.some(ws=>
                        ws?.appGroups.some(g=>g.groupState.metaWindows.includes(w))))};
            })()
            """.replace("__PID__", str(os.getpid())),
        )
        assert state and state["type"] == state["dock"], state
        assert state["skip_taskbar"] and not state["interesting"], state
        assert state["panel_instances"] > 0, state
        assert not state["panel"] and not state["switcher"], state
        assert state["application"] is None, state
        self.observations["dock_role"] = state

    def workspaces(self):
        self.window.config.current_workspace_only = True
        self.act("beta", "w.change_workspace_by_index(1, false)")
        self.backend.windows.refresh()
        assert len(self.backend.windows.list_windows("lab-alpha.desktop")) == 1
        assert not self.backend.windows.list_windows("lab-beta.desktop")
        self.capture("before-workspace-click")
        self.click_item("applet://workspaces")
        self.wait(
            lambda: (
                _eval(
                    self.shell, "global.workspace_manager.get_active_workspace_index()"
                )
                == 1
            )
        )
        self.wait(lambda: bool(self.backend.windows.list_windows("lab-beta.desktop")))
        assert not self.backend.windows.list_windows("lab-alpha.desktop")
        self.act("alpha", "w.stick()")
        self.backend.windows.refresh()
        assert self.backend.windows.list_windows("lab-alpha.desktop")
        self.capture("workspace-sticky")
        self.act("alpha", "w.unstick(); w.change_workspace_by_index(0, false)")
        assert self.backend.workspaces.activate("0").succeeded
        self.window.config.current_workspace_only = False
        self.backend.windows.refresh()
        self.act("beta", "w.change_workspace_by_index(0, false)")
        self.observations["workspaces"] = True

    def previews(self):
        self.act("beta", "imports.ui.main.activateWindow(w)")
        self.backend.windows.refresh()
        target = self.backend.windows.list_windows("lab-alpha.desktop")[0]
        for name, code in [
            ("inactive", ""),
            ("minimized", "w.minimize()"),
            (
                "other-workspace",
                "w.unminimize(); w.change_workspace_by_index(1, false)",
            ),
        ]:
            if code:
                self.act("alpha", code)
            before = _eval(
                self.shell, "global.display.focus_window?.get_stable_sequence()"
            )
            image = self.backend.previews.capture(target.id, width=200, height=150)
            assert image and image.width == 200 and image.height == 150
            pixbuf = image.image
            data = pixbuf.get_pixels()
            offset = 75 * pixbuf.get_rowstride() + 100 * pixbuf.get_n_channels()
            assert tuple(data[offset : offset + 3]) == (255, 0, 0), name
            assert (
                _eval(self.shell, "global.display.focus_window?.get_stable_sequence()")
                == before
            )
            pixbuf.savev(str(self.evidence / f"preview-{name}.png"), "png", [], [])
        self.act("alpha", "w.change_workspace_by_index(0, false)")
        anchor = self.window.get_hover_anchor(desktop_id="lab-alpha.desktop")
        self.motion(anchor[0] + 24, anchor[1] + 24)
        self.wait(lambda: self.window.preview.get_visible())
        self.capture("hover-preview")
        if self.backend.name == "cinnamon-xwayland":
            popup = self.window.preview
            x, y = popup.get_position()
            width, height = popup.get_size()
            assert x >= 0 and x + width <= 1280, (x, width)
            assert y >= 0 and y + height <= anchor[1] + 3, (y, height, anchor)
            self.observations["popup_coordinates"] = [x, y, width, height]
        self.motion(20, 20)
        self.window.preview.hide()
        self.settle()
        self.observations["previews"] = True

    def desktop(self):
        original = sorted((r["id"], r["minimized"]) for r in self.state())
        self.click_item("applet://desktop")
        self.wait(lambda: all(not r["visible"] for r in self.state()))
        self.capture("desktop-hidden")
        self.click_item("applet://desktop")
        self.wait(lambda: all(r["visible"] for r in self.state()))
        assert original == sorted((r["id"], r["minimized"]) for r in self.state())
        for show in (True, True):
            assert self.backend.desktop_actions.show_desktop(show).succeeded
            self.settle()
            assert all(not r["visible"] for r in self.state())
        for show in (False, False):
            assert self.backend.desktop_actions.show_desktop(show).succeeded
            self.settle()
            assert all(r["visible"] for r in self.state())
        self.observations["desktop"] = True

    def idle(self):
        before = self.backend.idle.idle_seconds()
        self.settle(0.7)
        after = self.backend.idle.idle_seconds()
        assert after > before + 0.4
        self.motion(40, 40)
        self.settle(0.1)
        reset = self.backend.idle.idle_seconds()
        assert reset < after
        applet = self.model.get_applet("applet://deskpresence")
        assert applet and applet._idle_service is self.backend.idle
        applet._tick()
        self.observations["idle_seconds"] = [before, after, reset]

    def colors(self):
        rect = self.row("alpha")["rect"]

        def sample():
            self.capture("picker-active")
            self.click(rect[0] + 200, rect[1] + 150)

        self.later(sample)
        self.click_item("applet://colorpicker")
        applet = self.model.get_applet("applet://colorpicker")
        assert applet._hex == "#FF0000", applet._hex
        assert applet._overlay is None
        previous = applet._hex
        self.later(self.escape)
        self.click_item("applet://colorpicker")
        assert applet._hex == previous
        self.later(self.backend.screen_capture.stop)
        self.click_item("applet://colorpicker")
        assert applet._hex == previous
        assert _eval(self.shell, "Object.keys(global._dockingPicks || {}).length") == 0
        assert _eval(self.shell, "Object.keys(global._dockingColors || {}).length") == 0
        self.observations["color"] = applet._hex

    def dodge(self):
        self.motion(20, 20)
        self.act(
            "alpha",
            "imports.ui.main.activateWindow(w); "
            "w.move_resize_frame(true, 100, 430, 900, 260)",
        )
        self.backend.windows.refresh()
        anchor = self.window.get_hover_anchor(desktop_id="applet://desktop")
        point = (int(anchor[0] + 24), int(anchor[1] + 24))
        visible = self.capture("dodge-overlapping-visible")
        self.window.config.hide_mode = "dodge-active"
        self.window.autohide.reset()
        self.wait(lambda: self.window.autohide.hide_offset > 0.95)
        hidden = self.capture("dodge-hidden")

        def pixel(image):
            offset = (
                point[1] * image.get_rowstride() + point[0] * image.get_n_channels()
            )
            return tuple(image.get_pixels()[offset : offset + 3])

        assert pixel(hidden) == (255, 0, 0), (point, pixel(hidden), self.row("alpha"))
        assert pixel(visible) != pixel(hidden)
        self.act("alpha", "w.move_resize_frame(true, 100, 100, 400, 300)")
        self.wait(lambda: self.window.autohide.hide_offset < 0.05)
        self.capture("dodge-visible")
        self.window.config.hide_mode = "none"
        self.window.autohide.reset()
        self.observations["dodge"] = True

    def picking(self, alpha, beta):
        self.act("alpha", "imports.ui.main.activateWindow(w)")
        rect = self.row("alpha")["rect"]
        picker = self.backend.window_picker
        target = picker.pick_window_at(x=rect[0] + 200, y=rect[1] + 150)
        assert target and target.title == "Native services alpha"
        assert picker.pid_for(target.id) == alpha.pid
        self.later(self.escape)
        self.click_item("applet://windowkiller")
        assert alpha.poll() is None and beta.poll() is None
        self.later(lambda: self.click(rect[0] + 200, rect[1] + 150))
        self.click_item("applet://windowkiller")
        self.wait(lambda: alpha.poll() is not None)
        assert beta.poll() is None
        assert picker.pid_for(target.id) is None
        assert not picker.kill(target.id).succeeded
        xwayland = self.spawn("xwayland", xwayland=True)
        row = self.row("xwayland")
        assert row["client_type"] == 1, row
        self.act("xwayland", "imports.ui.main.activateWindow(w)")
        row = self.row("xwayland")
        selected = picker.pick_window_at(x=row["rect"][0] + 100, y=row["rect"][1] + 100)
        assert selected and selected.title == "Native services xwayland"
        assert picker.pid_for(selected.id) == xwayland.pid
        self.later(lambda: self.click(row["rect"][0] + 100, row["rect"][1] + 100))
        self.click_item("applet://windowkiller")
        self.wait(lambda: xwayland.poll() is not None)
        assert beta.poll() is None
        self.observations["picking"] = True

    def client_cleanup(self):
        # Simulate a client crash while it owns a modal compositor selection.
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "from docking.platform.backends.cinnamon.shell "
                    "import CinnamonShellClient;"
                    "from docking.platform.backends.cinnamon.selection "
                    "import CinnamonSelection;"
                    "CinnamonSelection(client=CinnamonShellClient.connect())"
                    ".select(pick_body='return [x,y];')"
                ),
            ]
        )
        self.children.append(process)
        self.wait(
            lambda: (
                _eval(self.shell, "Object.keys(global._dockingPicks || {}).length") == 1
            )
        )
        process.kill()
        process.wait(timeout=5)
        self.wait(
            lambda: (
                _eval(self.shell, "Object.keys(global._dockingPicks || {}).length") == 0
            )
        )
        self.click_item("applet://workspaces")
        self.wait(
            lambda: (
                _eval(
                    self.shell, "global.workspace_manager.get_active_workspace_index()"
                )
                == 1
            )
        )
        assert self.backend.workspaces.activate("0").succeeded
        self.observations["client_crash_cleanup"] = True

    def finish(self):
        for process in self.children:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
        (self.evidence / "services.json").write_text(
            json.dumps(self.observations, indent=2)
        )


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(sys.argv[2])
        return
    from docking.core.config import Config, PinnedEntry

    applications = Path(os.environ["XDG_DATA_HOME"]) / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    for name in ("alpha", "beta"):
        (applications / f"lab-{name}.desktop").write_text(
            f"[Desktop Entry]\nType=Application\nName=Lab {name}\n"
            f"Exec={sys.executable} {__file__} --child {name}\n"
            f"StartupWMClass=lab-{name}\n"
        )
    Config(
        pinned=[PinnedEntry("app", f"lab-{name}.desktop") for name in ("alpha", "beta")]
        + [
            PinnedEntry("applet", f"applet://{name}")
            for name in (
                "workspaces",
                "desktop",
                "deskpresence",
                "colorpicker",
                "windowkiller",
            )
        ],
        startup_tips_enabled=False,
        update_check_enabled=False,
        zoom_enabled=False,
        hide_delay_ms=0,
        unhide_delay_ms=0,
    ).save()
    original = app._start_runtime
    failed = []

    def start(items, model, backend):
        original(items, model, backend)
        probe = Probe(items, model, backend)

        def run():
            try:
                probe.run()
            except Exception:
                probe.capture("failure")
                failed.append(traceback.format_exc())
                print(failed[-1], flush=True)
            finally:
                probe.finish()
                Gtk.main_quit()
            return False

        GLib.timeout_add(1500, run)
        return False

    app._start_runtime = start
    app.main()
    if failed:
        raise SystemExit(1)
    print("All seven Cinnamon service regressions passed", flush=True)


if __name__ == "__main__":
    main()
