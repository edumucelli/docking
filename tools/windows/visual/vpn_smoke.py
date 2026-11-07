"""Exercise native VPN menus, credentials, real tunnel traffic and teardown.

Requires owned lab profiles listed in profiles.json beside --fixture. The
fixture JSON supplies a private IKEv2 endpoint, credentials and an HTTP token.
Profile and certificate provisioning/cleanup are separate from this GUI run.
Credentials are never put in the report, command line or screenshots.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import asdict
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python")
    output = args.output.resolve()
    if (output / "result.json").exists():
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    owned = json.loads(
        (args.fixture.parent / "profiles.json").read_text(encoding="utf-8")
    )
    assert len(owned) == 2 and all(p["name"].startswith("Docking ") for p in owned)
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
    from docking.applets.network.implementations.windows.ras import ras_api
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
    api = ras_api()
    assert not any(w["title"] == "Docking" for w in native.windows())
    profiles = [
        p for p in api.profiles() if p.identifier in {r["identifier"] for r in owned}
    ]
    assert len(profiles) == 2
    assert not any(
        any(p.name in w["title"] for p in profiles) for w in native.windows()
    ), "Close owned test dialers before starting acceptance"
    user = next(p for p in profiles if not p.all_users)
    system = next(p for p in profiles if p.all_users)
    assert not any(s.matches(p) for s in api.sessions() for p in profiles)
    native.key(0x1B)
    native.user.SetCursorPos(1100, 100)
    baseline = native.workarea()
    report = {"baseline_workarea": baseline, "profiles": [asdict(p) for p in profiles]}
    shared = {}
    errors = []
    ready = threading.Event()
    original = app.build_dock_window

    def build(**kwargs):
        ui = original(**kwargs)
        shared.update(ui=ui, model=kwargs["model"])
        ready.set()
        return ui

    app.build_dock_window = build

    def call(callback):
        done = threading.Event()
        values = []
        failures = []

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

    def sessions():
        return [s for s in api.sessions() if s.matches(user)]

    def drive():
        try:
            assert ready.wait(90)
            network = call(lambda: shared["model"].get_applet("applet://network"))
            assert network is not None
            shared["network"] = network
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
                    if g.item.desktop_id == "applet://network"
                )
                window = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                return window["rect"][0] + rect.x + rect.w / 2, window["rect"][
                    1
                ] + rect.y + rect.h / 2

            def menu():
                native.click(*call(point), right=True)
                root = native.wait_for(
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
                        for r in root.get_children()
                        if r.get_visible()
                        and r.get_sensitive()
                        and not isinstance(r, Gtk.SeparatorMenuItem)
                    ]
                )
                labels = call(lambda: [r.get_label() for r in rows])
                native.key(0x24)
                for _ in range(labels.index("VPN Connections")):
                    native.key(0x28)
                native.key(0x27)
                return call(
                    lambda: next(
                        r.get_submenu()
                        for r in root.get_children()
                        if r.get_label() == "VPN Connections"
                    )
                )

            def state_menu(name):
                submenu = menu()
                values = call(
                    lambda: [
                        {
                            "label": r.get_label(),
                            "active": r.get_active(),
                            "sensitive": r.get_sensitive(),
                        }
                        for r in submenu.get_children()
                    ]
                )
                report.setdefault("menu_states", {})[name] = values
                capture(name)
                native.key(0x1B)
                native.key(0x1B)
                return values

            def choose(profile):
                submenu = menu()
                labels = call(lambda: [r.get_label() for r in submenu.get_children()])
                native.key(0x24)
                for _ in range(labels.index(profile.name)):
                    native.key(0x28)
                native.key(0x0D)

            def dialer(profile):
                window = native.wait_for(
                    lambda: next(
                        (
                            w
                            for w in native.windows()
                            if w["title"] == "Connect " + profile.name
                        ),
                        None,
                    ),
                    "Native profile dialog did not open",
                    30,
                )
                assert native.user.GetForegroundWindow() == window["hwnd"], (
                    "Native dialer did not receive foreground"
                )
                # IsWindowVisible precedes completion of the native opening
                # animation. Wait before capturing or using screen coordinates.
                time.sleep(0.8)
                window = next(
                    w for w in native.windows() if w["hwnd"] == window["hwnd"]
                )
                assert native.user.GetForegroundWindow() == window["hwnd"]
                report.setdefault("dialer_windows", []).append(window)
                return window

            def close_dialog(window):
                native.key(0x1B)
                native.wait_for(
                    lambda: (
                        not any(w["hwnd"] == window["hwnd"] for w in native.windows())
                    ),
                    "Cancel did not close the owned dialer",
                )

            def connect():
                choose(user)
                window = dialer(user)
                x, y = window["rect"][:2]
                native.click(x + 215, y + 203)
                native.key(0x41, control=True)
                native.key(0x08)
                native.key(0x09)
                native.key(0x41, control=True)
                native.key(0x08)
                native.key(0x09, shift=True)
                # Windows may retain the username from an earlier connection.
                # Capture both fields empty, before entering either credential.
                capture("native-credential-dialog")
                native.type_text(fixture["username"])
                native.key(0x09)
                native.type_text(fixture["password"])
                native.click(x + 57, y + 381)
                session = native.wait_for(
                    lambda: next((s for s in sessions() if s.connected), None),
                    "Native IKEv2 connection did not become active",
                    70,
                )
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(
                    "http://"
                    + fixture["address"]
                    + ":"
                    + str(fixture["port"])
                    + "/token",
                    timeout=15,
                ) as response:
                    assert response.read().decode() == fixture["token"], (
                        "Unexpected tunneled HTTP response"
                    )
                return session

            time.sleep(2)
            values = state_menu("vpn-inactive-menu")
            assert len(values) == 2 and not any(r["active"] for r in values)
            choose(system)
            window = dialer(system)
            capture("system-profile-native-dialog")
            close_dialog(window)
            assert not sessions()
            report["system_profile_cancel"] = True
            choose(user)
            window = dialer(user)
            close_dialog(window)
            assert not sessions()
            report["user_profile_cancel"] = True
            session = connect()
            report["session"] = asdict(session)
            report["tunneled_http"] = True
            values = state_menu("vpn-connected-menu")
            assert next(r for r in values if r["label"] == user.name)["active"]
            # Independent OS oracle, separate from the ctypes binding being tested.
            name = user.name.replace("'", "''")
            command = (
                "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
                "$p=Get-VpnConnection -Name '" + name + "';"
                "[ordered]@{Name=$p.Name;Guid=$p.Guid.ToString();"
                "ConnectionStatus=$p.ConnectionStatus.ToString();"
                "SplitTunneling=$p.SplitTunneling}|ConvertTo-Json -Compress"
            )
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    command,
                ],
                capture_output=True,
                encoding="utf-8",
                check=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=30,
            )
            oracle = json.loads(result.stdout)
            assert (
                oracle["ConnectionStatus"] == "Connected"
                and oracle["Guid"].strip("{}").casefold() == user.identifier
            )
            report["independent_vpn"] = oracle
            # Disconnect using the actual dock menu and wait for its native worker.
            choose(user)
            native.wait_for(
                lambda: not sessions(), "Dock did not disconnect the owned VPN", 20
            )
            native.wait_for(
                lambda: call(
                    lambda: not network._backend.connection_manager._vpn._workers
                ),
                "Disconnect worker did not publish completion",
                20,
            )
            values = state_menu("vpn-disconnected-menu")
            assert not any(r["active"] for r in values)
            report["dock_disconnect"] = True
            # Repeat an authentic connection, then change it outside the applet.
            connect()
            assert (
                sum(p.name.casefold() == user.name.casefold() for p in api.profiles())
                == 1
            )
            command = [
                str(Path(os.environ["SYSTEMROOT"]) / "System32/rasdial.exe"),
                user.name,
                "/disconnect",
            ]
            external = subprocess.run(
                command,
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=30,
            )
            assert external.returncode == 0, "External Windows disconnect failed"
            report["external_disconnect_output"] = external.stdout
            native.wait_for(
                lambda: not sessions(), "External disconnect did not finish", 20
            )
            values = state_menu("vpn-external-disconnected-menu")
            assert not any(r["active"] for r in values)
            report["external_disconnect_observed"] = True
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            capture("failure")
        finally:
            # Only this fixture's sessions/dialogs may be touched by the driver.
            remaining = sessions()
            if remaining:
                api.disconnect(remaining)
            for window in native.windows():
                if any(p.name in window["title"] for p in profiles):
                    native.user.PostMessageW(window["hwnd"], 0x10, 0, 0)
            if ready.is_set():

                def close():
                    grab = Gtk.grab_get_current()
                    if isinstance(grab, Gtk.MenuShell):
                        grab.cancel()
                    shared["ui"].window.close()

                call(close)

    driver = threading.Thread(target=drive, name="windows-vpn-smoke", daemon=True)
    driver.start()
    try:
        app.main()
        driver.join(15)
        assert not driver.is_alive()
        assert native.workarea() == baseline
        binding = WindowsHotkeyAPI()
        binding.register(MOD_CONTROL | MOD_ALT, 0x20)
        binding.unregister()
        assert shared["network"]._timer_id == 0
        assert not sessions()
        report["normal_shutdown"] = True
        if errors:
            raise errors[0]
    finally:
        (output / "result.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
