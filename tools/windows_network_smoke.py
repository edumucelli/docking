"""Verify native Network data, traffic, menus and settings on the Windows desktop.

Prepare traffic-source.bin in the fresh output directory on the host. The tool
copies only this owned fixture over SMB, verifies byte deltas and visible speed
updates, and removes its source/upload payloads after the check.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python")
    output = args.output.resolve()
    if (output / "result.json").exists():
        parser.error("Choose a fresh output directory")
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
    from docking.applets.network.windows import WindowsNetworkBackend, _IPHelper
    from docking.platform.backends.windows.hotkeys import (
        MOD_ALT,
        MOD_CONTROL,
        WindowsHotkeyAPI,
    )
    from tools.windows_desktop_driver import WindowsDesktop

    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://network"},
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
        payload = output / "traffic-source.bin"
        uploaded = output / "traffic-upload.bin"
        transfer_errors = []
        transfer = None
        try:
            assert payload.is_file() and payload.stat().st_size == 8 * 1024 * 1024
            assert ready.wait(90)
            ui, model = shared["ui"], shared["model"]
            network = call(lambda: model.get_applet("applet://network"))
            assert network is not None
            shared["network"] = network
            assert isinstance(network._backend, WindowsNetworkBackend)
            native.wait_for(
                lambda: call(lambda: network._state.is_connected),
                "The Network applet did not detect the native connection",
            )
            initial = call(lambda: vars(network._state).copy())
            report["initial_state"] = initial
            assert initial["interface_id"] and initial["ip_address"]
            oracle = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    (
                        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
                        "$rows=@(Get-NetAdapter | ForEach-Object {"
                        "$n=$_;$s=$n | Get-NetAdapterStatistics;"
                        "$ips=@(Get-NetIPAddress -InterfaceIndex $n.ifIndex "
                        "-ErrorAction SilentlyContinue | "
                        "Select-Object -ExpandProperty IPAddress);"
                        "[pscustomobject]@{name=$n.Name;id=$n.InterfaceGuid.ToString();"
                        "index=$n.ifIndex;status=$n.Status;addresses=$ips;"
                        "rx=$s.ReceivedBytes;tx=$s.SentBytes}});"
                        "ConvertTo-Json -InputObject $rows -Depth 4 -Compress"
                    ),
                ],
                capture_output=True,
                encoding="utf-8",
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=45,
            )
            rows = json.loads(oracle.stdout)
            selected = next(
                row
                for row in rows
                if row["id"].strip("{}").casefold()
                == initial["interface_id"].strip("{}").casefold()
            )
            assert selected["name"] == initial["iface"]
            assert (
                selected["status"] == "Up"
                and initial["ip_address"] in selected["addresses"]
            )
            report["independent_adapter"] = selected
            api = _IPHelper()

            def counters():
                return next(
                    a.counters
                    for a in api.adapters()
                    if a.identifier == initial["interface_id"]
                )

            before = counters()
            assert before is not None
            assert before.rx >= selected["rx"] and before.tx >= selected["tx"]
            report["counter_before"] = list(before)
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
                    if g.item.desktop_id == "applet://network"
                )
                current = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                return current["rect"][0] + rect.x + rect.w / 2, current["rect"][
                    1
                ] + rect.y + rect.h / 2

            def hover(name):
                native.click(1100, 100)
                time.sleep(0.4)
                native.user.SetCursorPos(*map(int, call(point)))
                time.sleep(1)
                assert (
                    call(lambda: ui.window.hover.hovered_item.desktop_id)
                    == "applet://network"
                )
                assert call(lambda: ui.window.tooltip._tooltip_window.get_visible())
                report.setdefault("tooltips", {})[name] = call(
                    lambda: ui.window.tooltip._last_name
                )
                capture(name)

            def choose(label):
                native.click(*call(point), right=True)
                menu = native.wait_for(
                    lambda: call(
                        lambda: (
                            g
                            if isinstance(g := Gtk.grab_get_current(), Gtk.Menu)
                            else None
                        )
                    ),
                    "Network menu did not open",
                )
                rows = call(
                    lambda: [
                        r
                        for r in menu.get_children()
                        if r.get_visible()
                        and r.get_sensitive()
                        and not isinstance(r, Gtk.SeparatorMenuItem)
                    ]
                )
                labels = call(lambda: [r.get_label() for r in rows])
                report["menu_labels"] = labels
                native.key(0x24)
                for _ in range(labels.index(label)):
                    native.key(0x28)
                native.key(0x0D)

            hover("network-connected")
            choice = call(
                lambda: next(
                    label
                    for label, key in network._backend.interface_choices()
                    if key == initial["interface_id"]
                )
            )
            choose(choice)
            assert call(lambda: network._backend.interface) == initial["interface_id"]
            choose("Show Upload")
            assert call(lambda: network._speed_overlay) == "upload"
            saved = json.loads(config.read_text(encoding="utf-8"))["applet_prefs"][
                "network"
            ]
            assert saved == {
                "interface": initial["interface_id"],
                "speed_overlay": "upload",
            }
            report["persisted_explicit_selection"] = saved

            def copy_payload():
                try:
                    with (
                        payload.open("rb", buffering=0) as src,
                        uploaded.open("wb", buffering=0) as dest,
                    ):
                        while chunk := src.read(256 * 1024):
                            dest.write(chunk)
                            os.fsync(dest.fileno())
                            time.sleep(0.2)
                except Exception as exc:
                    transfer_errors.append(exc)

            transfer = threading.Thread(
                target=copy_payload, name="native-network-traffic"
            )
            transfer.start()
            samples = []
            deadline = time.monotonic() + 30
            captured = False
            while transfer.is_alive() and time.monotonic() < deadline:
                sample = call(lambda: vars(network._state).copy())
                samples.append(sample)
                if sample["tx_speed"] > 1024 and not captured:
                    hover("network-uploading")
                    captured = True
                time.sleep(0.4)
            transfer.join(10)
            assert not transfer.is_alive() and not transfer_errors, transfer_errors
            after = counters()
            assert after is not None
            report["counter_after"] = list(after)
            oracle_after = subprocess.run(
                oracle.args,
                capture_output=True,
                encoding="utf-8",
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=45,
            )
            final_oracle = next(
                row
                for row in json.loads(oracle_after.stdout)
                if row["id"].strip("{}").casefold()
                == initial["interface_id"].strip("{}").casefold()
            )
            report["independent_adapter_after"] = final_oracle
            assert after.rx <= final_oracle["rx"] and after.tx <= final_oracle["tx"]
            report["traffic_samples"] = samples
            assert after.rx - before.rx >= payload.stat().st_size
            assert after.tx - before.tx >= payload.stat().st_size
            assert captured and any(s["tx_speed"] > 1024 for s in samples)
            assert uploaded.stat().st_size == payload.stat().st_size
            choose("Hide Speeds")
            assert call(lambda: network._speed_overlay) == "none"
            choose("Auto")
            assert call(lambda: network._backend.interface) == ""
            report["persisted_auto_selection"] = json.loads(
                config.read_text(encoding="utf-8")
            )["applet_prefs"]["network"]
            assert report["persisted_auto_selection"] == {
                "interface": "",
                "speed_overlay": "none",
            }
            hover("network-auto")
            choose("Connection Information")
            settings = native.wait_for(
                lambda: next(
                    (w for w in native.windows() if w["title"] == "Settings"), None
                ),
                "Network Settings did not open",
                60,
            )
            time.sleep(5)
            report["settings"] = settings
            capture("network-settings")
            choose("Edit Connections...")
            time.sleep(4)
            capture("network-advanced-settings")
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            capture("failure")
        finally:
            if transfer is not None:
                transfer.join(10)
            if transfer is None or not transfer.is_alive():
                for path in (uploaded, payload):
                    path.unlink(missing_ok=True)
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

    driver = threading.Thread(target=drive, name="windows-network-smoke", daemon=True)
    driver.start()
    try:
        app.main()
        driver.join(10)
        assert not driver.is_alive()
        assert native.workarea() == baseline
        binding = WindowsHotkeyAPI()
        binding.register(MOD_CONTROL | MOD_ALT, 0x20)
        binding.unregister()
        assert shared["network"]._timer_id == 0
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
