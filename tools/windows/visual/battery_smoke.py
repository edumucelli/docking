"""Check native Battery and Power Settings, with optional labeled driver fixtures.

The VM normally has no battery. --exercise-fixtures also exercises the real
applet/rendering pipeline with injected PowerAPI snapshots; these captures do
not establish physical-battery or hotplug acceptance.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exercise-fixtures", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python")
    output = args.output.resolve()
    if (output / "result.json").exists():
        parser.error("Choose a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(output / "config")
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.applets.battery.backend import create_backend
    from docking.applets.battery.implementations.windows import backend as windows
    from docking.platform.native.windows.hotkeys import (
        MOD_ALT,
        MOD_CONTROL,
        WindowsHotkeyAPI,
    )
    from tools.windows.visual.desktop_driver import WindowsDesktop

    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://battery"},
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
    native = WindowsDesktop()
    assert not any(w["title"] in {"Docking", "Settings"} for w in native.windows())
    native.key(0x1B)
    native.user.SetCursorPos(1100, 100)
    baseline = native.workarea()
    report, shared, errors = {"baseline_workarea": baseline}, {}, []
    ready = threading.Event()
    original_build = app.build_dock_window
    original_api = windows._native_api

    def build(**kwargs):
        ui = original_build(**kwargs)
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
        try:
            assert ready.wait(90)
            ui, model = shared["ui"], shared["model"]
            battery = call(lambda: model.get_applet("applet://battery"))
            assert battery is not None
            shared["battery"] = battery
            assert isinstance(create_backend(), windows.WindowsBatteryBackend)
            api = windows.WindowsPowerAPI()
            power, rate = api.power_status(), api.battery_state()
            assert power is not None and rate is not None
            report["native_power_status"] = {
                name: getattr(power, name) for name, _kind in power._fields_
            }
            report["native_battery_state"] = {
                name: getattr(rate, name)
                for name, _kind in rate._fields_
                if name != "Spare1"
            }
            oracle = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    (
                        "Add-Type -AssemblyName System.Windows.Forms;"
                        "$p=[System.Windows.Forms.SystemInformation]::PowerStatus;"
                        "$b=@(Get-CimInstance Win32_Battery | "
                        "Select-Object Name,BatteryStatus,EstimatedChargeRemaining);"
                        "[ordered]@{ac=$p.PowerLineStatus.ToString();"
                        "flags=[int]$p.BatteryChargeStatus;"
                        "percent=$p.BatteryLifePercent;seconds=$p.BatteryLifeRemaining;"
                        "batteries=$b} | ConvertTo-Json -Depth 4 -Compress"
                    ),
                ],
                capture_output=True,
                text=True,
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=45,
            )
            report["independent_power_status"] = json.loads(oracle.stdout)
            assert report["independent_power_status"]["flags"] == power.BatteryFlag
            if power.BatteryFlag != 255 and power.BatteryFlag & 128:
                assert call(lambda: battery._state) is None
                assert not report["independent_power_status"]["batteries"]
                assert call(lambda: battery.item.name) == "No battery"
            report["native_applet_state"] = call(
                lambda: battery._state._asdict() if battery._state else None
            )
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock did not map",
            )
            shared["dock_hwnd"] = dock["hwnd"]

            def point():
                frame = ui.window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://battery"
                )
                current = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                return (
                    current["rect"][0] + rect.x + rect.w / 2,
                    current["rect"][1] + rect.y + rect.h / 2,
                )

            def hover(name):
                native.click(1100, 100)
                time.sleep(0.5)
                target = tuple(map(int, call(point)))
                native.user.SetCursorPos(*target)
                position = wintypes.POINT()
                assert native.user.GetCursorPos(ctypes.byref(position))
                report.setdefault("hover_points", {})[name] = {
                    "target": target,
                    "actual": [position.x, position.y],
                }
                time.sleep(1.5)
                report["hover_points"][name]["gtk"] = call(
                    lambda: {
                        "dock_hovered": ui.window.dock_hovered,
                        "cursor": [ui.window.cursor_x, ui.window.cursor_y],
                        "item": (
                            ui.window.hover.hovered_item.desktop_id
                            if ui.window.hover.hovered_item
                            else None
                        ),
                        "tooltip_text": ui.window.tooltip._last_name,
                        "tooltip_visible": bool(
                            ui.window.tooltip._tooltip_window
                            and ui.window.tooltip._tooltip_window.get_visible()
                        ),
                    }
                )
                capture(name)
                assert report["hover_points"][name]["gtk"]["item"] == "applet://battery"
                assert report["hover_points"][name]["gtk"]["tooltip_visible"]

            def choose(label, *, capture_name=None):
                native.click(*call(point), right=True)
                menu = native.wait_for(
                    lambda: call(
                        lambda: (
                            grab
                            if isinstance(grab := Gtk.grab_get_current(), Gtk.Menu)
                            else None
                        )
                    ),
                    "Battery menu did not open",
                )
                labels = call(
                    lambda: [
                        row.get_label()
                        for row in menu.get_children()
                        if row.get_visible()
                        and row.get_sensitive()
                        and not isinstance(row, Gtk.SeparatorMenuItem)
                    ]
                )
                report["menu_labels"] = labels
                if capture_name is not None:
                    capture(capture_name)
                index = labels.index(label)
                native.key(0x24)
                for _ in range(index):
                    native.key(0x28)
                native.key(0x0D)

            time.sleep(2)
            hover("battery-native")
            assert call(lambda: battery._timer_id) > 0
            choose("Percentage")
            native.wait_for(
                lambda: call(lambda: battery._overlay) == "percent",
                "Percentage preference did not change",
            )
            if args.exercise_fixtures:
                report["fixtures"] = {
                    "source": "Injected PowerAPI snapshots, not physical hardware",
                    "states": [],
                }

                def inject(
                    name,
                    *,
                    percent,
                    flags,
                    ac,
                    seconds,
                    rate,
                    peripherals=(),
                    delayed=False,
                ):
                    native.wait_for(
                        lambda: call(lambda: not battery._worker._active_keys),
                        "Previous peripheral query did not finish",
                        20,
                    )
                    query_started, query_release = threading.Event(), threading.Event()

                    def read_peripherals():
                        if delayed:
                            query_started.set()
                            assert query_release.wait(20), (
                                "Slow fixture was not released"
                            )
                        return list(peripherals)

                    fake = SimpleNamespace(
                        power_status=lambda: windows.SystemPowerStatus(
                            ACLineStatus=ac,
                            BatteryFlag=flags,
                            BatteryLifePercent=percent,
                            BatteryLifeTime=seconds,
                            BatteryFullLifeTime=0xFFFFFFFF,
                        ),
                        battery_state=lambda: windows.SystemBatteryState(
                            BatteryPresent=1, Rate=rate
                        ),
                        peripheral_batteries=read_peripherals,
                    )

                    def poll():
                        windows._native_api = lambda: fake
                        battery._tick()
                        return battery._state._asdict()

                    value = call(poll)
                    if delayed:
                        assert query_started.wait(5), "Peripheral worker did not start"
                        try:
                            choose(
                                "No overlay", capture_name="fixture-query-pending-menu"
                            )
                            assert call(lambda: battery._overlay) == "none"
                            report["menu_while_peripheral_query_pending"] = True
                        finally:
                            query_release.set()
                    native.wait_for(
                        lambda: call(lambda: not battery._worker._active_keys),
                        "Peripheral query did not reach the UI",
                        20,
                    )
                    tooltip = call(lambda: battery.item.name)
                    report["fixtures"]["states"].append(
                        {"name": name, "state": value, "tooltip": tooltip}
                    )
                    hover(name)
                    return value

                value = inject(
                    "fixture-discharging",
                    percent=67,
                    flags=1,
                    ac=0,
                    seconds=8040,
                    rate=-7600,
                )
                assert value["capacity"] == 67 and value["power_watts"] == 7.6
                from docking.applets.battery.implementations.windows import peripherals

                _parse_response = peripherals._parse_response

                peripherals = _parse_response(
                    {
                        "devices": [
                            {
                                "id": "00000000-0000-0000-0000-000000000001",
                                "name": "Fixture mouse",
                                "connected": True,
                                "local_machine": False,
                                "percent": 10,
                                "charging": 0,
                                "categories": ["Mouse"],
                            },
                            {
                                "id": "00000000-0000-0000-0000-000000000002",
                                "name": "Fixture headset",
                                "connected": True,
                                "local_machine": False,
                                "percent": 25,
                                "charging": 1,
                                "categories": ["Headset"],
                            },
                        ]
                    }
                )
                inject(
                    "fixture-peripheral-batteries",
                    percent=67,
                    flags=1,
                    ac=0,
                    seconds=8040,
                    rate=-7600,
                    peripherals=peripherals,
                    delayed=True,
                )
                tooltip = call(lambda: battery.item.name)
                assert "Fixture mouse: 10%" in tooltip
                assert "Fixture headset: 25% (charging)" in tooltip
                report["fixtures"]["peripherals"] = {
                    "source": "Device-container properties injected through PowerAPI",
                    "readings": [item._asdict() for item in peripherals],
                    "tooltip": tooltip,
                }
                choose("Power (W)")
                assert call(lambda: battery._power_timer_id) > 0
                hover("fixture-power-overlay")
                choose("Percentage")
                assert call(lambda: battery._power_timer_id) == 0
                value = inject(
                    "fixture-charging",
                    percent=43,
                    flags=8,
                    ac=1,
                    seconds=0xFFFFFFFF,
                    rate=12000,
                )
                assert value["status"] == "Charging"
                assert value["seconds_remaining"] is None
                value = inject(
                    "fixture-plugged-idle",
                    percent=80,
                    flags=1,
                    ac=1,
                    seconds=8040,
                    rate=0,
                )
                assert value["status"] == "Not charging"
                value = inject(
                    "fixture-unknown",
                    percent=255,
                    flags=255,
                    ac=255,
                    seconds=0xFFFFFFFF,
                    rate=-0x80000000,
                )
                assert value["capacity"] is None and value["power_watts"] is None

                def restore():
                    windows._native_api = original_api
                    battery._tick()

                call(restore)
                hover("battery-native-restored")
            saved = json.loads(config.read_text(encoding="utf-8"))["applet_prefs"]
            assert saved["battery"]["overlay"] == "percent"
            report["persisted_preferences"] = saved["battery"]
            choose("Power Settings")
            settings = native.wait_for(
                lambda: next(
                    (w for w in native.windows() if w["title"] == "Settings"), None
                ),
                "Power Settings did not open",
                60,
            )
            time.sleep(5)
            settings = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows()
                        if w["title"] == "Settings"
                        and w["pid"] == settings["pid"]
                        and w["rect"][2] - w["rect"][0] > 400
                    ),
                    None,
                ),
                "Power Settings did not finish mapping",
            )
            report["settings"] = settings
            capture("battery-power-settings")
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            capture("failure")
        finally:
            windows._native_api = original_api
            if settings is not None and any(
                w["hwnd"] == settings["hwnd"] and w["pid"] == settings["pid"]
                for w in native.windows()
            ):
                native.user.PostMessageW(settings["hwnd"], 0x10, 0, 0)
            if ready.is_set():

                def close():
                    grab = Gtk.grab_get_current()
                    if isinstance(grab, Gtk.MenuShell):
                        grab.cancel()
                    shared["ui"].window.close()

                call(close)

    driver = threading.Thread(target=drive, name="windows-battery-smoke", daemon=True)
    driver.start()
    try:
        app.main()
        driver.join(10)
        assert not driver.is_alive()
        assert native.workarea() == baseline
        binding = WindowsHotkeyAPI()
        binding.register(MOD_CONTROL | MOD_ALT, 0x20)
        binding.unregister()
        battery = shared["battery"]
        assert battery._timer_id == 0 and battery._power_timer_id == 0
        report["normal_shutdown"] = True
        if errors:
            raise errors[0]
    finally:
        temporary = output / "result.json.tmp"
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(output / "result.json")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
