"""Native Bluetooth UI acceptance in the owned, adapter-free Windows VM.

Actual hardware queries and Settings run first. Radio transitions, device data
and policy states are explicit injected fixtures; no physical radios are changed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import asdict, replace
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
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.applets.bluetooth.windows import WindowsBluetoothBackend
    from docking.applets.bluetooth.windows_radio import (
        ClassicDevice,
        Radio,
        address_text,
    )
    from tools.windows.visual.desktop_driver import WindowsDesktop
    from tools.windows.visual.powerprofiles_smoke import powershell

    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline = native.workarea()
    report = {"physical_radio_writes": False, "states": {}, "fixtures": {}}
    errors, shared = [], {}
    ready = threading.Event()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://bluetooth"},
                    {"kind": "applet", "target": "applet://clock"},
                ],
                "applet_prefs": {"bluetooth": {"continuous_discovery": True}},
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

    class FixtureRadio:
        def __init__(self):
            self.radios = (
                Radio(
                    "explicit-bluetooth-fixture",
                    0x112233445566,
                    "Fixture Bluetooth",
                    1,
                    (
                        ClassicDevice(
                            0xAABBCCDDEEFF, "Fixture headphones", 0x0404, True, True
                        ),
                        ClassicDevice(
                            0xAABBCCDDEE11, "Fixture keyboard", 0x0500, True, False
                        ),
                    ),
                ),
            )
            self.writes, self.denied = [], False
            self.removals = []
            self.discovery_starts, self.discovery_stops = [], []
            self.closed = False

        def close(self):
            self.closed = True
            self.radios = tuple(replace(r, discovering=False) for r in self.radios)

        def start_discovery(self, radio):
            self.discovery_starts.append([radio.identifier, radio.address])
            if self.denied:
                return False
            self.radios = tuple(
                replace(r, discovering=True) if r.identifier == radio.identifier else r
                for r in self.radios
            )
            return True

        def stop_discovery(self, identifier):
            self.discovery_stops.append(identifier)
            self.radios = tuple(
                replace(r, discovering=False) if r.identifier == identifier else r
                for r in self.radios
            )
            return True

        def snapshot(self):
            self.radios = tuple(
                replace(r, discovering=r.discovering and r.state == 1)
                for r in self.radios
            )
            return self.radios

        def set_power(self, radio, powered):
            self.writes.append([radio.identifier, powered])
            if self.denied:
                return False
            self.radios = tuple(
                replace(r, state=1 if powered else 2)
                if r.identifier == radio.identifier
                else r
                for r in self.radios
            )
            return True

        def remove_pairing(self, radio, device):
            self.removals.append([radio.identifier, device.address])
            if self.denied:
                return False
            self.radios = tuple(
                replace(
                    current,
                    devices=tuple(
                        replace(item, paired=False)
                        if item.address == device.address
                        else item
                        for item in current.devices
                    ),
                )
                if current.identifier == radio.identifier
                else current
                for current in self.radios
            )
            return True

    def capture(name):
        ImageGrab.grab(all_screens=True).save(output / (name + ".png"))

    def drive():
        settings = None
        try:
            assert ready.wait(90)
            applet = call(lambda: shared["model"].get_applet("applet://bluetooth"))
            shared["applet"] = applet
            assert isinstance(applet._backend, WindowsBluetoothBackend)
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
                    if g.item.desktop_id == "applet://bluetooth"
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
                        not applet._power_transition_in_progress
                        and not applet._worker._active_keys
                    )
                )

            def state():
                return call(lambda: applet._state)

            def menu():
                native.click(*call(point), right=True)
                popup = native.wait_for(
                    lambda: call(lambda: Gtk.grab_get_current()),
                    "Bluetooth menu did not map",
                )
                assert isinstance(popup, Gtk.Menu)
                rows = call(
                    lambda: [
                        {
                            "label": row.get_label(),
                            "enabled": row.get_sensitive(),
                            "submenu": row.get_submenu() is not None,
                        }
                        for row in popup.get_children()
                        if row.get_label()
                    ]
                )
                return popup, rows

            def choose(popup, label, *, submenu=False):
                native.key(0x24)
                for _ in range(40):
                    selected = call(lambda: popup.get_selected_item())
                    if (
                        selected
                        and call(lambda selected=selected: selected.get_label())
                        == label
                    ):
                        native.key(0x27 if submenu else 0x0D)
                        if submenu:
                            child = call(
                                lambda selected=selected: selected.get_submenu()
                            )
                            native.wait_for(
                                lambda child=child: call(lambda: child.get_mapped()),
                                "Device submenu did not map",
                            )
                            return child
                        return None
                    native.key(0x28)
                raise AssertionError(f"Menu item {label} did not receive selection")

            # Independent PnP oracle, without writing adapter or pairing state.
            pnp = powershell(r"""
$rows=@(Get-CimInstance Win32_PnPEntity | Where-Object {$_.PNPClass -eq 'Bluetooth'} |
 ForEach-Object {@{name=$_.Name;id=$_.PNPDeviceID;status=$_.Status}});
ConvertTo-Json -InputObject $rows -Compress
""")
            assert pnp == [], "This verifier requires a VM without Bluetooth hardware"
            report["pnp_oracle"] = pnp
            native.wait_for(idle, "Native Bluetooth discovery did not finish", 25)
            assert not state().available and not state().error
            report["states"]["native"] = asdict(state())
            popup, rows = menu()
            assert any(
                r["label"] == "Bluetooth unavailable" and not r["enabled"] for r in rows
            )
            assert any(r["label"] == "Devices..." and r["enabled"] for r in rows)
            report["native_menu"] = rows
            capture("native-unavailable-menu")
            assert not any(w["title"] == "Settings" for w in native.windows())
            choose(popup, "Devices...")
            settings = native.wait_for(
                lambda: next(
                    (w for w in native.windows() if w["title"] == "Settings"), None
                ),
                "Bluetooth Settings did not open",
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
            report["settings_controls"] = controls
            assert any(
                "bluetooth" in row["name"].lower() and row["type"] == "ControlType.Text"
                for row in controls
            ), "Settings did not reach a Bluetooth page"
            assert any(
                r["name"] == "Devices" and r["type"] == "ControlType.Text"
                for r in controls
            )
            assert any(
                r["id"] == "SystemSettings_Device_Discovery_Button" for r in controls
            )
            capture("native-bluetooth-settings")
            native.user.PostMessageW(settings["hwnd"], 0x10, 0, 0)
            native.wait_for(
                lambda: (
                    not any(w["hwnd"] == settings["hwnd"] for w in native.windows())
                ),
                "Owned Settings did not close",
            )
            settings = None
            publish()

            fixture = FixtureRadio()
            shared["fixture"] = fixture

            def install():
                applet._backend.close()
                applet._poll_generation += 1
                applet._backend = WindowsBluetoothBackend(api=fixture)
                applet._refresh_now()

            call(install)
            native.wait_for(
                lambda: idle() and state().available, "Fixture did not reach UI"
            )
            popup, rows = menu()
            labels = [row["label"] for row in rows]
            assert (
                "Continuous Discovery" in labels and "Recent Connections" not in labels
            )
            assert "Turn Bluetooth Off" in labels and "Devices..." in labels
            device_rows = [
                row
                for row in rows
                if row["label"].startswith(("Fixture headphones", "Fixture keyboard"))
            ]
            assert len(device_rows) == 2
            assert all(row["submenu"] and row["enabled"] for row in device_rows)
            report["fixture_menu"] = rows
            capture("fixture-connected-menu")
            keyboard = next(
                row["label"]
                for row in rows
                if row["label"].startswith("Fixture keyboard")
            )
            keyboard_device = next(
                device
                for device in state().devices
                if device.address == address_text(0xAABBCCDDEE11)
            )
            submenu = choose(popup, keyboard, submenu=True)
            actions = call(lambda: [row.get_label() for row in submenu.get_children()])
            assert actions == ["Remove Pairing"]
            capture("fixture-remove-pairing-menu")
            choose(submenu, "Remove Pairing")
            native.wait_for(
                lambda: (
                    idle()
                    and any(
                        device.path == keyboard_device.path and not device.paired
                        for device in state().devices
                    )
                ),
                "Fixture pairing removal readback failed",
            )
            assert fixture.removals == [["explicit-bluetooth-fixture", 0xAABBCCDDEE11]]
            report["fixture_pairing_removal"] = {
                "actions": actions,
                "writes": fixture.removals,
                "readback_unpaired": True,
                "state": asdict(state()),
                "scope": "injected classic-device fixture; no physical unpairing",
            }
            native.wait_for(
                lambda: idle() and state().adapters[0].discovering,
                "Fixture discovery was not observed",
                15,
            )
            popup, _rows = menu()
            capture("fixture-discovering-menu")
            choose(popup, "Continuous Discovery")
            native.wait_for(
                lambda: idle() and not state().adapters[0].discovering,
                "Fixture discovery did not stop",
                15,
            )
            assert fixture.discovery_stops[-1] == "explicit-bluetooth-fixture"
            popup, _rows = menu()
            choose(popup, "Continuous Discovery")
            native.wait_for(
                lambda: idle() and state().adapters[0].discovering,
                "Fixture discovery did not restart",
                15,
            )
            assert fixture.discovery_starts[-1] == [
                "explicit-bluetooth-fixture",
                0x112233445566,
            ]
            report["fixture_discovery"] = {
                "starts": fixture.discovery_starts,
                "stops": fixture.discovery_stops,
                "scope": "explicit injected radio fixture; no physical inquiry",
                "restarted": True,
            }
            popup, _rows = menu()
            choose(popup, "Turn Bluetooth Off")
            native.wait_for(
                lambda: idle() and not state().adapters[0].powered,
                "Fixture power-off readback failed",
            )
            native.click(*call(point))
            native.wait_for(
                lambda: idle() and state().adapters[0].powered,
                "Fixture click power-on failed",
            )
            fixture.denied = True
            native.click(*call(point))
            native.wait_for(
                lambda: idle() and call(lambda: bool(applet._action_error)),
                "Denied fixture action did not settle",
            )
            assert state().adapters[0].powered
            report["fixture_denied"] = asdict(state())
            fixture.denied = False
            fixture.radios = (replace(fixture.radios[0], state=2, devices=()),)
            native.wait_for(
                lambda: idle() and not state().adapters[0].powered,
                "External fixture change was not polled",
                15,
            )
            fixture.radios = (replace(fixture.radios[0], state=3),)
            native.wait_for(
                lambda: idle() and not state().adapters[0].power_controllable,
                "Policy-disabled fixture was not observed",
                15,
            )
            popup, rows = menu()
            assert any(
                r["label"] == "Turn Bluetooth On" and not r["enabled"] for r in rows
            )
            capture("fixture-policy-disabled-menu")
            native.key(0x1B)
            before = list(fixture.writes)
            native.click(*call(point))
            time.sleep(0.3)
            assert fixture.writes == before
            fixture.radios = ()
            native.wait_for(
                lambda: idle() and not state().available,
                "Removed adapter was not observed",
                15,
            )
            report["fixtures"] = {
                "writes": fixture.writes,
                "denied_readback": True,
                "external_poll": True,
                "policy_disabled": True,
                "adapter_removed": True,
            }
            report["ok"] = True
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
            publish()

    driver = threading.Thread(
        target=drive, name="windows-bluetooth-smoke", daemon=False
    )
    driver.start()
    try:
        app.main()
        driver.join(90)
        assert not driver.is_alive()
        assert native.workarea() == baseline
        assert shared["applet"]._stopped and shared["applet"]._poll_id == 0
        if "fixture" in shared:
            fixture = shared["fixture"]
            assert fixture.closed and not any(r.discovering for r in fixture.radios)
            assert not shared["applet"]._local_discovery_paths
            report["discovery_sessions_released"] = True
        report["normal_shutdown"] = True
        if errors:
            raise errors[0]
    finally:
        publish()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
