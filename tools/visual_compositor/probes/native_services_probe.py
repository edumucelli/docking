"""Native services and independently sampled compositor pixels on a real dock."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
import types
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk


def fixture():
    GLib.set_prgname("lab-native")
    window = Gtk.Window(title="Native backend fixture")
    window.set_decorated(False)
    window.set_default_size(1280, 720)
    area = Gtk.DrawingArea()

    def draw(_widget, cr):
        cr.set_source_rgb(1, 0, 0)
        cr.paint()
        return False

    area.connect("draw", draw)
    window.add(area)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    if not os.environ.get("NIRI_SOCKET"):
        window.maximize()
    Gtk.main()


class Probe:
    def __init__(self, window, backend, position):
        self.window, self.backend, self.position = window, backend, position
        self.evidence = Path(os.environ["LAB_DIR"])
        self.child = None

    @types.coroutine
    def settle(self, seconds=0.2):
        # Return to GTK's main loop: recursive iteration inside a timeout can
        # suppress frame-clock painting on nested compositors.
        yield seconds

    async def wait(self, predicate, message):
        end = time.monotonic() + 8
        while time.monotonic() < end:
            if predicate():
                return
            await self.settle(0.05)
        if self.backend.name == "niri":
            for command in ("outputs", "workspaces", "windows"):
                subprocess.run(["niri", "msg", "-j", command], check=True)
        raise AssertionError(f"{message}: {self.backend.windows.list_all_windows()}")

    async def capture(self, phase):
        # Occluded nested surfaces may stop getting frame callbacks, including
        # while held visible. A fresh compositor capture and the pixel assertions
        # below, rather than a callback on the client, prove the visible result.
        self.window.queue_draw()
        await self.settle(0.4)
        target = self.evidence / f"{self.position}-{phase}.png"
        mode = os.environ.get("LAB_CAPTURE_MODE", "wayland")
        if mode == "x11":
            env = {
                **os.environ,
                "GDK_BACKEND": "x11",
                "DISPLAY": (self.evidence / "outer-display").read_text().strip(),
                "XAUTHORITY": (self.evidence / "outer-authority").read_text().strip(),
            }
            subprocess.run(
                [sys.executable, __file__, "--capture-x11", str(target)],
                env=env,
                check=True,
            )
        else:
            env = dict(os.environ)
            if mode == "parent":
                env["WAYLAND_DISPLAY"] = os.environ["LAB_PARENT_DISPLAY"]
            subprocess.run(
                ["grim", "-s", "1", str(target)], env=env, check=True, timeout=5
            )
        return GdkPixbuf.Pixbuf.new_from_file(str(target))

    @staticmethod
    def pixel(image, point):
        offset = point[1] * image.get_rowstride() + point[0] * image.get_n_channels()
        return tuple(image.get_pixels()[offset : offset + 3])

    async def run(self):
        assert self.backend.name == os.environ["LAB_EXPECTED_BACKEND"]
        if self.position == "idle":
            assert self.backend.capabilities.supports_idle_time
            assert self.backend.idle is not None
            await self.wait(
                lambda: self.backend.idle.idle_seconds() is not None,
                "Idle event not delivered",
            )
            before = self.backend.idle.idle_seconds()
            await self.settle(0.3)
            after = self.backend.idle.idle_seconds()
            assert after > before
            print(
                json.dumps({"backend": self.backend.name, "idle_seconds": after}),
                flush=True,
            )
            return
        assert self.backend.capabilities.supports_overlap_active
        assert self.backend.capabilities.supports_overlap_any
        self.window.autohide.set_disabled(True, reason="native visual probe")
        self.window.autohide.set_hovered(False)
        await self.wait(
            lambda: self.window.autohide.hide_offset < 0.05, "Dock not shown"
        )
        await self.settle()
        from docking.ui.display import window_screen_position

        origin = window_screen_position(self.window)
        rect = self.window.geometry.build_frame().static_dock_rect
        self.child = subprocess.Popen([sys.executable, __file__, "--fixture"])
        await self.wait(
            lambda: any(
                row.title == "Native backend fixture" and row.geometry is not None
                for row in self.backend.windows.list_all_windows()
            ),
            "Native fixture not tracked with geometry",
        )
        await self.settle(0.5)
        row = next(
            row
            for row in self.backend.windows.list_all_windows()
            if row.title == "Native backend fixture"
        )
        assert self.backend.windows.activate(row.id).succeeded
        await self.wait(
            lambda: any(
                window.id == row.id and window.active and window.visible
                for window in self.backend.windows.list_all_windows()
            ),
            "Fixture not activated",
        )
        await self.settle(0.4)
        left = max(origin.x + rect.x, row.geometry.x)
        top = max(origin.y + rect.y, row.geometry.y)
        right = min(origin.x + rect.x + rect.w, row.geometry.right)
        bottom = min(origin.y + rect.y + rect.h, row.geometry.bottom)
        assert right > left and bottom > top
        visible = await self.capture("overlapping-visible")
        self.window.autohide.set_disabled(False, reason="native visual probe")
        await self.wait(
            lambda: self.window.autohide.hide_offset > 0.999, "Dock did not dodge"
        )
        hidden = await self.capture("hidden")
        # Locate actual dock pixels inside the fixture's edge band. Mutter may
        # constrain ordinary dock windows below a panel even when GTK reports
        # the requested origin; that request is not an independent pixel oracle.
        bounds = row.geometry
        x0, y0 = max(0, bounds.x), max(0, bounds.y)
        x1 = min(visible.get_width(), bounds.right)
        y1 = min(visible.get_height(), bounds.bottom)
        band = 163
        if self.position == "left":
            x1 = min(x1, x0 + band)
        elif self.position == "right":
            x0 = max(x0, x1 - band)
        elif self.position == "top":
            y1 = min(y1, y0 + band)
        else:
            y0 = max(y0, y1 - band)
        visible_data, hidden_data = visible.get_pixels(), hidden.get_pixels()
        stride, channels = visible.get_rowstride(), visible.get_n_channels()
        hidden_stride, hidden_channels = hidden.get_rowstride(), hidden.get_n_channels()
        changed, painted = 0, 0
        for y in range(y0, y1):
            for x in range(x0, x1):
                offset, hidden_offset = (
                    y * stride + x * channels,
                    y * hidden_stride + x * hidden_channels,
                )
                red, green, blue = visible_data[offset : offset + 3]
                # The fixture launcher is blue. Ignore unchanged white/black
                # cursor pixels and shell chrome outside the dock.
                if blue > red + 20 and blue > green:
                    painted += 1
                    changed += (
                        hidden_data[hidden_offset : hidden_offset + 3]
                        == b"\xff\x00\x00"
                    )
        pixels_passed = changed >= max(32, painted * 0.98)
        if os.environ.get("LAB_SERVICE_MODE") != "services":
            assert pixels_passed, (
                "Dock pixels did not disappear",
                changed,
                painted,
                row,
            )
        if row.can_preview:
            preview = self.backend.previews.capture(row.id, width=240, height=160)
            if preview is not None:
                preview.image.savev(
                    str(self.evidence / f"{self.position}-preview.png"), "png", [], []
                )
        assert self.backend.windows.activate(row.id).succeeded
        if self.backend.name == "kwin":
            assert self.backend.desktop_actions.show_desktop().succeeded
            await self.wait(
                lambda: (
                    not any(
                        window.visible
                        for window in self.backend.windows.list_all_windows()
                    )
                ),
                "Show Desktop retained visible windows",
            )
            assert self.backend.desktop_actions.show_desktop().succeeded
            await self.wait(
                lambda: any(
                    window.visible for window in self.backend.windows.list_all_windows()
                ),
                "Show Desktop did not restore windows",
            )
        if row.can_minimize:
            assert self.backend.windows.minimize_all("lab-native.desktop").succeeded
            await self.wait(
                lambda: self.window.autohide.hide_offset < 0.05,
                "Minimized window still hides dock",
            )
            await self.capture("minimized-visible")
            assert self.backend.windows.activate(row.id).succeeded
            await self.wait(
                lambda: self.window.autohide.hide_offset > 0.95,
                "Activated window did not hide dock",
            )
        await self.check_workspace(row)
        assert self.backend.windows.close(row.id).succeeded
        await self.wait(
            lambda: (
                not any(
                    row.title == "Native backend fixture"
                    for row in self.backend.windows.list_all_windows()
                )
            ),
            "Closed window still tracked",
        )
        await self.wait(
            lambda: self.window.autohide.hide_offset < 0.05,
            "Closed window still hides dock",
        )
        await self.capture("restored")
        idle = self.backend.idle.idle_seconds() if self.backend.idle else None
        result = {
            "backend": self.backend.name,
            "position": self.position,
            "dodge_pixels": pixels_passed,
            "dodge_policy": True,
            "actions": True,
            "workspace_switch_restore": True,
            "idle_seconds": idle,
            "window_geometry": [
                row.geometry.x,
                row.geometry.y,
                row.geometry.width,
                row.geometry.height,
            ],
        }
        (self.evidence / f"{self.position}-services.json").write_text(
            json.dumps(result, indent=2)
        )
        print(json.dumps(result), flush=True)

    async def check_workspace(self, fixture_window):
        workspaces = self.backend.workspaces
        assert workspaces is not None
        original = workspaces.active_workspace()
        assert original is not None
        if self.backend.name == "sway":
            from docking.platform.backends.wayland.sway_ipc import SwayIpcClient

            assert (
                SwayIpcClient(os.environ["SWAYSOCK"])
                .command('workspace "docking-lab-other"')
                .succeeded
            )
            await self.wait(
                lambda: workspaces.active_workspace().id != original.id,
                "Sway did not create another workspace",
            )
        other = next(
            row for row in workspaces.list_workspaces() if row.id != original.id
        )
        assert workspaces.activate(other.id).succeeded
        await self.wait(
            lambda: any(
                row.id == fixture_window.id and row.visible is False
                for row in self.backend.windows.list_all_windows()
            ),
            "Off-workspace window is still visible",
        )
        await self.wait(
            lambda: self.window.autohide.hide_offset < 0.05,
            "Off-workspace window still hides dock",
        )
        await self.capture("other-workspace-visible")
        if self.backend.name in {"kwin", "sway"}:
            self.window.config.current_workspace_only = True
            self.backend.windows.refresh()
            assert not self.backend.windows.list_windows("lab-native.desktop")
        assert self.backend.windows.activate(fixture_window.id).succeeded
        await self.wait(
            lambda: (
                workspaces.active_workspace().id == original.id
                and any(
                    row.id == fixture_window.id and row.active and row.visible
                    for row in self.backend.windows.list_all_windows()
                )
            ),
            "Activation did not restore the fixture's workspace",
        )
        self.window.config.current_workspace_only = False
        self.backend.windows.refresh()
        assert self.backend.windows.list_windows("lab-native.desktop")
        await self.wait(
            lambda: self.window.autohide.hide_offset > 0.95,
            "Restored workspace does not hide dock",
        )

    def close(self):
        if self.child is not None:
            self.child.terminate()
            self.child.wait(timeout=3)


def main():
    if "--fixture" in sys.argv:
        fixture()
        return
    if "--capture-x11" in sys.argv:
        root = Gdk.get_default_root_window()
        width, height = root.get_width(), root.get_height()
        Gdk.pixbuf_get_from_window(root, 0, 0, width, height).savev(
            sys.argv[2], "png", [], []
        )
        return
    from docking.launcher import prepare_display

    prepare_display()
    from docking import app
    from docking.core.config import Config, PinnedEntry

    position = sys.argv[1]
    applications = Path(os.environ["XDG_DATA_HOME"]) / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    (applications / "lab-native.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Native fixture\nExec=/usr/bin/true\n"
        "StartupWMClass=lab-native\nIcon=application-x-executable\n"
    )
    Config(
        pinned=[PinnedEntry("app", "lab-native.desktop")],
        position="bottom" if position == "idle" else position,
        hide_mode="dodge-active",
        hide_delay_ms=0,
        unhide_delay_ms=0,
        zoom_enabled=False,
        startup_tips_enabled=False,
        update_check_enabled=False,
    ).save()
    original = app._start_runtime
    failures = []

    def start(items, model, backend):
        original(items, model, backend)
        probe = Probe(items._window, backend, position)
        task = probe.run()

        def run():
            try:
                delay = task.send(None)
                GLib.timeout_add(max(1, round(delay * 1000)), run)
                return False
            except StopIteration:
                pass
            except Exception:
                failures.append(traceback.format_exc())
                print(failures[-1], flush=True)
            probe.close()
            Gtk.main_quit()
            return False

        GLib.timeout_add(1000, run)
        return False

    app._start_runtime = start
    app.main()
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
