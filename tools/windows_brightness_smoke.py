"""Native Brightness UI acceptance on the owned, backlight-free Windows VM.

First verifies actual unavailable hardware. Subsequent DDC/panel scenarios use
explicit injected provider fixtures and never write native monitor settings.
Mouse/wheel/menu interaction and dock shutdown use the real Windows desktop.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python in an owned test VM")
    output = args.output.resolve()
    if (output / "result.json").exists():
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(output / "config")
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.applets.brightness.windows import (
        Display,
        MonitorBrightness,
        WindowsBrightnessBackend,
    )
    from tools.windows_desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline = native.workarea()
    report = {"physical_writes": False, "states": {}, "fixtures": {}}
    errors, shared = [], {}
    ready = threading.Event()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://brightness"},
                    {"kind": "applet", "target": "applet://clock"},
                ],
                "applet_prefs": {"brightness": {"show_level": True}},
                "icon_size": 64,
                "hide_mode": "none",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    def publish():
        temporary = output / "result.tmp"
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(output / "result.json")

    original = app.build_dock_window

    def build(**kwargs):
        ui = original(**kwargs)
        shared.update(ui=ui, model=kwargs["model"])
        ready.set()
        return ui

    app.build_dock_window = build

    def call(callback):
        done, values, failures = threading.Event(), [], []

        def invoke():
            try:
                values.append(callback())
            except Exception as exc:
                failures.append(exc)
            finally:
                done.set()
            return False

        GLib.idle_add(invoke)
        assert done.wait(20), "GTK did not service an observation"
        if failures:
            raise failures[0]
        return values[0]

    class FixtureMonitor:
        def __init__(self, provider):
            self.provider = provider
            self.level = 0.5 if provider == "ddc" else 0.4
            self.levels = (
                tuple(i / 100 for i in range(10, 101))
                if provider == "ddc"
                else (0.2, 0.4, 0.6, 1.0)
            )
            self.identity = "explicit-brightness-" + provider + "-fixture"
            self.connected, self.denied = True, False
            self.writes = []

        def primary(self):
            return (
                Display(123, "Fixture primary monitor", self.identity)
                if self.connected
                else None
            )

        def access(self, provider, display, value):
            if provider != self.provider or not self.connected:
                return None
            if value is not None:
                assert display.identity == self.identity
                self.writes.append(value)
                if not self.denied:
                    self.level = min(self.levels, key=lambda level: abs(value - level))
            return MonitorBrightness(self.level, self.levels)

        def ddc(self, display, *, value=None):
            return self.access("ddc", display, value)

        def wmi(self, display, *, value=None):
            return self.access("wmi", display, value)

    def drive():
        try:
            assert ready.wait(90)
            applet = call(lambda: shared["model"].get_applet("applet://brightness"))
            shared["applet"] = applet
            assert isinstance(applet._backend, WindowsBrightnessBackend)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock did not map",
            )

            def point():
                frame = shared["ui"].window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://brightness"
                )
                window = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                return window["rect"][0] + rect.x + rect.w / 2, window["rect"][
                    1
                ] + rect.y + rect.h / 2

            def idle():
                return call(
                    lambda: (
                        not applet._set_in_progress
                        and "brightness-poll" not in applet._worker._active_keys
                    )
                )

            def wait_level(level):
                native.wait_for(
                    lambda: idle() and call(lambda: applet._brightness) == level,
                    "Brightness level did not settle",
                    20,
                )

            def capture(name):
                native.user.SetCursorPos(*map(int, call(point)))
                time.sleep(1.1)
                ImageGrab.grab(all_screens=True).save(output / (name + ".png"))
                report["states"][name] = call(
                    lambda: {
                        "state": asdict(applet._state),
                        "tooltip": applet.item.name,
                        "show_level": applet._show_level,
                    }
                )
                publish()

            native.wait_for(
                idle, "Initial native hardware discovery did not finish", 20
            )
            assert not call(lambda: applet._state.available), (
                "This verifier requires a VM without real brightness hardware"
            )
            capture("native-unavailable")
            native.click(*call(point))
            native.scroll(120)
            wait_level(None)
            assert call(lambda: applet.item.name) == "Brightness: Unavailable"
            report["native_unavailable_verified"] = True

            for provider in ("ddc", "wmi"):
                fixture = FixtureMonitor(provider)

                def install(fixture=fixture):
                    applet._backend.stop()
                    applet._poll_generation += 1
                    applet._backend = WindowsBrightnessBackend(api=fixture)
                    applet._tick()

                call(install)
                wait_level(fixture.level)
                capture("fixture-" + provider + "-initial")
                native.scroll(120)
                wait_level(0.52 if provider == "ddc" else 0.6)
                capture("fixture-" + provider + "-scroll")
                native.click(*call(point))
                wait_level(1.0)
                capture("fixture-" + provider + "-reset")
                fixture.denied = True
                native.scroll(-120)
                wait_level(1.0)
                fixture.denied = False
                fixture.level = 0.2
                wait_level(0.2)  # Real five-second timer observes external change.
                capture("fixture-" + provider + "-external")
                before = list(fixture.writes)
                fixture.identity += "-replacement"
                native.click(*call(point))  # Queued old-target reset must not write.
                native.wait_for(
                    lambda: (
                        idle()
                        and call(lambda: applet._state.target).endswith("-replacement")
                    ),
                    "Replacement display did not reach UI",
                )
                assert fixture.writes == before
                fixture.connected = False
                wait_level(None)
                capture("fixture-" + provider + "-removed")
                report["fixtures"][provider] = {
                    "writes": fixture.writes,
                    "denied_write_readback": True,
                    "replacement_rejected": True,
                    "disconnect_observed": True,
                }

            native.click(*call(point), right=True)
            menu = native.wait_for(
                lambda: call(lambda: Gtk.grab_get_current()),
                "Brightness menu did not map",
            )
            assert isinstance(menu, Gtk.Menu)
            rows = call(
                lambda: [
                    {
                        "label": r.get_label(),
                        "active": r.get_active()
                        if isinstance(r, Gtk.CheckMenuItem)
                        else None,
                    }
                    for r in menu.get_children()
                    if r.get_label()
                ]
            )
            assert any(r["label"] == "Show Level" and r["active"] for r in rows)
            native.key(0x24)
            native.key(0x0D)
            native.wait_for(
                lambda: call(lambda: not applet._show_level),
                "Native menu did not toggle Show Level",
            )
            persisted = json.loads(config.read_text(encoding="utf-8"))
            assert not persisted["applet_prefs"]["brightness"]["show_level"]
            report.update(ok=True, menu=rows, preference_persisted=True)
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            if ready.is_set():

                def close():
                    grab = Gtk.grab_get_current()
                    if isinstance(grab, Gtk.MenuShell):
                        grab.cancel()
                    shared["ui"].window.close()

                try:
                    call(close)
                except Exception as exc:
                    errors.append(exc)
                    report.update(ok=False, close_error=repr(exc))
            publish()

    driver = threading.Thread(
        target=drive, name="windows-brightness-smoke", daemon=False
    )
    driver.start()
    try:
        app.main()
        driver.join(90)
        assert not driver.is_alive()
        assert native.workarea() == baseline
        assert shared["applet"]._stopped and shared["applet"]._timer_id == 0
        report["normal_shutdown"] = True
        if errors:
            raise errors[0]
    finally:
        publish()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
