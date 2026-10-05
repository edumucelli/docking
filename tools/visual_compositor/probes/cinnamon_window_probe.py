"""Real dock clicks with independent Cinnamon window/focus observations."""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import time
from pathlib import Path
from uuid import uuid4

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from cinnamon_probe import _eval, _proxy, screenshot
from gi.repository import Gdk, Gio, GLib, Gtk

STATE_SCRIPT = """
(() => ({
    active_workspace: global.workspace_manager.get_active_workspace_index(),
    windows: global.get_window_actors().map(a => a.meta_window)
        .filter(w => w.get_workspace() &&
            ['lab-alpha', 'lab-beta'].includes(w.get_wm_class()))
        .map(w => ({id:w.get_stable_sequence(), app:w.get_wm_class(),
            pid:w.get_client_pid(),
            focused:w === global.display.focus_window, minimized:!!w.minimized,
            workspace:w.get_workspace().index()}))
}))()
"""


def client(name: str) -> None:
    GLib.set_prgname(f"lab-{name}")
    window = Gtk.Window(title=f"Lab taskbar {name} {uuid4()}")
    window.set_default_size(420, 300)
    window.add(Gtk.Label(label=f"Native Wayland taskbar client: {name}"))
    window.connect("destroy", Gtk.main_quit)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, window.destroy)
    window.show_all()
    Gtk.main()


class WindowProbe:
    def __init__(self, evidence: Path, name: str) -> None:
        self.evidence = evidence
        self.name = name
        self.shell = _proxy()
        self.dock = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.DO_NOT_AUTO_START,
            None,
            "org.docking.Docking",
            "/org/docking/Docking",
            "org.docking.Docking.Items1",
            None,
        )
        self.phases = {}

    def state(self):
        return _eval(self.shell, STATE_SCRIPT)

    def wait(self, predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = self.state()
            if predicate(state):
                return state
            time.sleep(0.1)
        raise AssertionError(f"window state did not converge: {self.state()}")

    def record(self, phase, predicate):
        self.phases[phase] = self.wait(predicate)
        screenshot(str(self.evidence / f"{self.name}.{phase}.png"))

    def click(self, app, button=272):
        found, x, y, _edge = self.dock.call_sync(
            "GetHoverAnchor",
            GLib.Variant("(s)", (f"lab-{app}.desktop",)),
            Gio.DBusCallFlags.NO_AUTO_START,
            1000,
            None,
        ).unpack()
        assert found, app
        width = _eval(self.shell, "global.display.get_monitor_geometry(0).width")
        height = _eval(self.shell, "global.display.get_monitor_geometry(0).height")
        input_path = Path(os.environ["XDG_RUNTIME_DIR"]) / "lab-input.sock"
        for pressed in (0, button):
            with socket.socket(socket.AF_UNIX) as connection:
                connection.settimeout(5)
                connection.connect(str(input_path))
                connection.sendall(
                    json.dumps(
                        {
                            "x": x + 24,
                            "y": y + 24,
                            "width": width,
                            "height": height,
                            "button": pressed,
                        }
                    ).encode()
                )
                assert json.loads(connection.recv(4096))["ok"]
            time.sleep(0.2)

    def act(self, sequence, code):
        assert type(sequence) is int
        assert (
            _eval(
                self.shell,
                "(() => {const w = global.get_window_actors().map(a => a.meta_window)"
                f".find(w => w.get_stable_sequence() === {sequence});"
                "if (!w || !['lab-alpha','lab-beta'].includes(w.get_wm_class()))"
                " return false;" + code + ";return true;})()",
            )
            is True
        )

    def run(self):
        assert type(Gdk.Display.get_default()).__name__ == "GdkWaylandDisplay"
        self.click("alpha")
        self.record("launched", lambda s: len(s["windows"]) == 1)
        self.click("beta")
        self.record(
            "other-app",
            lambda s: (
                len(s["windows"]) == 2
                and any(w["app"] == "lab-beta" and w["focused"] for w in s["windows"])
            ),
        )
        self.click("alpha")

        def alpha_focused(state):
            return len(state["windows"]) == 2 and any(
                w["app"] == "lab-alpha" and w["focused"] and not w["minimized"]
                for w in state["windows"]
            )

        self.record("switched", alpha_focused)
        self.click("alpha")
        self.record(
            "minimized",
            lambda s: (
                len(s["windows"]) == 2
                and any(
                    w["app"] == "lab-alpha" and w["minimized"] for w in s["windows"]
                )
            ),
        )
        self.click("alpha")
        self.record("restored", alpha_focused)
        alpha = next(w for w in self.state()["windows"] if w["app"] == "lab-alpha")
        self.act(alpha["id"], "w.change_workspace_by_index(1, false)")
        self.click("beta")
        self.wait(
            lambda s: (
                s["active_workspace"] == 0
                and any(w["app"] == "lab-beta" and w["focused"] for w in s["windows"])
            )
        )
        self.click("alpha")
        self.record(
            "workspace", lambda s: s["active_workspace"] == 1 and alpha_focused(s)
        )
        # The configured middle click deliberately opens a second app window.
        self.click("alpha", button=274)
        self.record(
            "multiple",
            lambda s: (
                len(s["windows"]) == 3
                and sum(w["app"] == "lab-alpha" for w in s["windows"]) == 2
            ),
        )
        self.click("beta")
        self.wait(
            lambda s: any(w["app"] == "lab-beta" and w["focused"] for w in s["windows"])
        )
        self.click("alpha")
        self.record(
            "multiple-switched",
            lambda s: (
                len(s["windows"]) == 3
                and any(w["app"] == "lab-alpha" and w["focused"] for w in s["windows"])
            ),
        )
        self.close_clients()
        self.record("closed", lambda s: not s["windows"])

    def close_clients(self):
        for window in self.state()["windows"]:
            # Ask each test client to destroy its own GTK window normally.
            assert type(window["pid"]) is int and window["pid"] > 1
            os.kill(window["pid"], signal.SIGUSR1)
            self.wait(
                lambda s, sequence=window["id"]: all(
                    w["id"] != sequence for w in s["windows"]
                )
            )

    def finish(self):
        (self.evidence / f"{self.name}.windows.json").write_text(
            json.dumps(
                {
                    "gtk_display_is_wayland": True,
                    "phases": self.phases,
                },
                indent=2,
            )
        )
        self.close_clients()
        _eval(
            self.shell,
            "global.workspace_manager.get_workspace_by_index(0)"
            ".activate(global.get_current_time()); true",
        )


def main():
    if sys.argv[1] == "--client":
        client(sys.argv[2])
        return
    probe = WindowProbe(Path(sys.argv[1]), sys.argv[2])
    try:
        probe.run()
    finally:
        probe.finish()


if __name__ == "__main__":
    main()
