"""Native Cam Shield absence/Settings acceptance and labeled rendering fixtures.

This verifier never opens a camera. Active, idle and unknown screenshots use
explicit API fixtures; they do not establish native camera-capture coverage.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expect-absent", action="store_true", required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python in an owned test VM")
    output = args.output.resolve()
    if (output / "result.json").exists():
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    cached = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json;from gi.repository import GLib;"
                "print(json.dumps(GLib.get_user_cache_dir()))"
            ),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    registries = list(
        (Path(json.loads(cached.stdout)) / "gstreamer-1.0").glob("registry.*.bin")
    )
    if not registries:
        parser.error("Prepare the complete native GStreamer registry first")
    target = output / "cache/gstreamer-1.0"
    target.mkdir(parents=True, exist_ok=True)
    for registry in registries:
        shutil.copy2(registry, target / registry.name)
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
    from docking.applets.camshield.windows import WindowsCamshieldBackend
    from docking.applets.camshield.windows_client import (
        CameraDevice,
        CameraReport,
        CameraSnapshot,
    )
    from tools.windows_desktop_driver import WindowsDesktop
    from tools.windows_powerprofiles_smoke import powershell

    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline = native.workarea()
    report = {"states": {}, "fixtures": {}, "native_capture_verified": False}
    errors, shared = [], {}
    ready = threading.Event()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://camshield"},
                    {"kind": "applet", "target": "applet://clock"},
                ],
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

    def capture(name):
        ImageGrab.grab(all_screens=True).save(output / (name + ".png"))

    def drive():
        settings = None
        sensor_processes = []
        try:
            assert ready.wait(90)
            applet = call(lambda: shared["model"].get_applet("applet://camshield"))
            shared["applet"] = applet
            assert isinstance(applet._backend, WindowsCamshieldBackend)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Cam Shield dock did not map",
            )

            def idle():
                return call(
                    lambda: (
                        not applet._worker._active_keys
                        and not applet._action_in_progress
                    )
                )

            def state():
                return call(lambda: applet._state)

            def point():
                window = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                frame = shared["ui"].window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://camshield"
                )
                return window["rect"][0] + rect.x + rect.w / 2, window["rect"][
                    1
                ] + rect.y + rect.h / 2

            def menu():
                native.click(*call(point), right=True)
                popup = native.wait_for(
                    lambda: call(Gtk.grab_get_current), "Camera menu did not map"
                )
                rows = call(
                    lambda: [
                        {"label": row.get_label(), "enabled": row.get_sensitive()}
                        for row in popup.get_children()
                        if row.get_label()
                    ]
                )
                for label in ("Lock Camera", "Unlock Camera"):
                    assert any(r["label"] == label and not r["enabled"] for r in rows)
                return popup, rows

            def dismiss():
                native.key(0x1B)
                native.wait_for(
                    lambda: call(Gtk.grab_get_current) is None,
                    "Camera menu did not dismiss",
                )

            def snapshot(name):
                report["states"][name] = asdict(state())
                capture(name)
                publish()

            def remember_sensor():
                process = applet._backend._api._process
                assert process is not None and process.poll() is None
                sensor_processes.append(process)
                report.setdefault("sensor_pids", []).append(process.pid)

            native.wait_for(idle, "Native camera query did not finish", 20)
            assert not state().available and not state().error
            call(remember_sensor)
            popup, rows = menu()
            assert any(r["label"] == "No camera devices found" for r in rows)
            report["native_menu"] = rows
            snapshot("native-absent-menu")

            assert not any(w["title"] == "Settings" for w in native.windows())
            assert any(r["label"] == "Camera Settings" and r["enabled"] for r in rows)
            native.key(0x24)
            for _ in range(30):
                selected = call(popup.get_selected_item)
                if selected and call(selected.get_label) == "Camera Settings":
                    native.key(0x0D)
                    break
                native.key(0x28)
            else:
                raise AssertionError("Camera Settings menu selection failed")
            settings = native.wait_for(
                lambda: next(
                    (w for w in native.windows() if w["title"] == "Settings"), None
                ),
                "Camera privacy Settings did not open",
                30,
            )
            time.sleep(2)
            controls = powershell(
                r"""
Add-Type -AssemblyName UIAutomationClient;Add-Type -AssemblyName UIAutomationTypes;
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]HWND);
$nodes=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
 [System.Windows.Automation.Condition]::TrueCondition);
$rows=@();foreach($node in $nodes){$v=$node.Current;
 $rows+=@{name=$v.Name;id=$v.AutomationId;type=$v.ControlType.ProgrammaticName}}
ConvertTo-Json -InputObject $rows -Depth 3 -Compress
""".replace("HWND", str(settings["hwnd"]))
            )
            assert any(
                r["name"] == "Camera" and r["type"] == "ControlType.Text"
                for r in controls
            )
            assert any("camera access" in r["name"].lower() for r in controls)
            report["settings_controls"] = controls
            capture("native-camera-settings")
            native.user.PostMessageW(settings["hwnd"], 0x10, 0, 0)
            native.wait_for(
                lambda: (
                    not any(w["hwnd"] == settings["hwnd"] for w in native.windows())
                ),
                "Owned Camera Settings did not close",
            )
            settings = None

            class Fixture:
                def __init__(self):
                    self.value = CameraSnapshot(
                        (
                            CameraDevice("front", "UI fixture Front"),
                            CameraDevice("rear", "UI fixture Rear"),
                        ),
                        (
                            CameraReport("front", (os.getpid(),)),
                            CameraReport("rear", (os.getpid(),)),
                        ),
                        True,
                    )
                    self.failed = False

                def snapshot(self):
                    if self.failed:
                        raise OSError("Explicit unavailable-provider UI fixture")
                    return self.value

                def stop(self):
                    pass

            fixture = Fixture()

            def install():
                applet._backend.stop()
                applet._generation += 1
                applet._backend = WindowsCamshieldBackend(api=fixture)
                applet._refresh_now()

            call(install)
            native.wait_for(
                lambda: idle() and state().active is True,
                "Active UI fixture did not arrive",
            )
            assert call(lambda: bool(applet._pulse_timer_id))
            phase = call(lambda: applet._pulse_phase)
            native.wait_for(
                lambda: call(lambda: applet._pulse_phase) != phase,
                "Fixture activity pulse did not advance",
            )
            popup, rows = menu()
            assert any("UI fixture Front, UI fixture Rear" in r["label"] for r in rows)
            snapshot("fixture-active-menu")
            dismiss()
            fixture.value = CameraSnapshot(
                fixture.value.devices,
                tuple(CameraReport(d.identifier, ()) for d in fixture.value.devices),
                True,
            )
            call(applet._refresh_now)
            native.wait_for(
                lambda: idle() and state().active is False,
                "Idle UI fixture did not arrive",
            )
            assert call(lambda: applet._pulse_timer_id) == 0
            popup, rows = menu()
            assert any(r["label"] == "Camera idle" for r in rows)
            snapshot("fixture-idle-menu")
            dismiss()
            fixture.value = CameraSnapshot(fixture.value.devices, (), False)
            call(applet._refresh_now)
            native.wait_for(
                lambda: idle() and state().active is None,
                "Unknown UI fixture did not arrive",
            )
            popup, rows = menu()
            assert any(r["label"] == "Camera activity unknown" for r in rows)
            assert not any(r["label"] == "Camera idle" for r in rows)
            snapshot("fixture-unknown-menu")
            dismiss()
            fixture.failed = True
            call(applet._refresh_now)
            native.wait_for(
                lambda: idle() and state().error, "Error UI fixture did not arrive"
            )
            popup, rows = menu()
            assert any(r["label"] == "Camera state unavailable" for r in rows)
            snapshot("fixture-error-menu")
            dismiss()
            native.wait_for(idle, "Fixture query did not finish")

            def restore():
                applet._backend.stop()
                applet._generation += 1
                applet._backend = WindowsCamshieldBackend()
                applet._refresh_now()

            call(restore)
            native.wait_for(
                lambda: idle() and not state().available and not state().error,
                "Native absence did not return",
                20,
            )
            call(remember_sensor)
            snapshot("native-restored-absence")
            report.update(
                ok=True,
                native_absence=True,
                fixtures={"active": True, "idle": True, "unknown": True, "error": True},
            )
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            capture("failure")
        finally:
            if settings is not None:
                native.user.PostMessageW(settings["hwnd"], 0x10, 0, 0)
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
            for process in sensor_processes:
                try:
                    assert process.wait(10) == 0
                except Exception as exc:
                    errors.append(exc)
                    report.update(ok=False, sensor_close_error=repr(exc))
            report["sensor_exits"] = [p.poll() for p in sensor_processes]
            publish()

    driver = threading.Thread(
        target=drive, name="windows-camshield-smoke", daemon=False
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
