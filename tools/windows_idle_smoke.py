"""Verify Windows Desk Presence against real input and an independent C# reader.

Runs an isolated dock with the supported 30-second idle threshold and the normal
10-second poll. No idle readings or timers are injected. Use a quiet test desktop;
outside input during the away interval legitimately resets the session clock.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

ORACLE = r"""
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class DockingIdleOracle {
    [StructLayout(LayoutKind.Sequential)]
    public struct LastInfo { public uint size; public uint time; }
    [DllImport("user32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetLastInputInfo(ref LastInfo info);
    [DllImport("kernel32.dll")]
    public static extern ulong GetTickCount64();
    public static uint Read() {
        LastInfo info = new LastInfo();
        info.size = (uint)Marshal.SizeOf(typeof(LastInfo));
        if (!GetLastInputInfo(ref info))
            throw new System.ComponentModel.Win32Exception();
        return info.time;
    }
}
'@
$last=[DockingIdleOracle]::Read()
$now=[DockingIdleOracle]::GetTickCount64()
[pscustomobject]@{
    last=$last;uptime=$now;seconds=(($now-$last) -band [uint64]4294967295)/1000.0
    size=[Runtime.InteropServices.Marshal]::SizeOf([type]'DockingIdleOracle+LastInfo')
    session=[Diagnostics.Process]::GetCurrentProcess().SessionId
} | ConvertTo-Json -Compress
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires an owned interactive Windows desktop")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(output / "config")
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    repo = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.platform.backends.windows.idle import WindowsIdleService
    from tools.windows_desktop_driver import WindowsDesktop

    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://deskpresence"},
                    {"kind": "applet", "target": "applet://clock"},
                ],
                "icon_size": 64,
                "hide_mode": "none",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
                "applet_prefs": {"deskpresence": {"idle_threshold_s": 30}},
            }
        ),
        encoding="utf-8",
    )
    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline = native.workarea()
    shared, errors, report = {}, [], {"pid": os.getpid(), "baseline_workarea": baseline}
    ready = threading.Event()
    original = app.build_dock_window

    def build(**kwargs):
        ui = original(**kwargs)
        shared.update(ui=ui, model=kwargs["model"], backend=kwargs["session_backend"])
        ready.set()
        return ui

    app.build_dock_window = build

    def publish():
        (output / "result.json").write_text(json.dumps(report, indent=2))

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

    def independent_input():
        program = (
            "import sys,os;sys.path.insert(0," + repr(str(repo)) + ");"
            "from tools.windows_desktop_driver import WindowsDesktop;"
            "WindowsDesktop().click(1100,100);print(os.getpid())"
        )
        value = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        pid = int(value.stdout.strip())
        assert pid != os.getpid()
        report.setdefault("input_processes", []).append(pid)

    def oracle():
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                base64.b64encode(ORACLE.encode("utf-16-le")).decode("ascii"),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        value = json.loads(result.stdout.lstrip("\ufeff"))
        sample = shared["backend"].idle.idle_seconds()
        assert sample is not None and value["size"] == 8
        assert abs(sample - value["seconds"]) < 1.0, (sample, value)
        return {"backend_seconds": sample, "independent": value}

    def drive():
        dock = None
        try:
            assert ready.wait(90)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Idle dock did not map",
            )
            applet = call(lambda: shared["model"].get_applet("applet://deskpresence"))
            service = shared["backend"].idle
            assert isinstance(service, WindowsIdleService)
            assert shared["backend"].capabilities.supports_idle_time
            assert call(lambda: applet._idle_service is service)
            assert call(lambda: applet._state.idle_threshold_s) == 30

            def state():
                return call(
                    lambda: {
                        "presence": applet._state.presence.value,
                        "at_desk": applet._state.at_desk_seconds,
                        "away": applet._state.away_seconds,
                        "pulse": bool(applet._pulse_timer_id),
                    }
                )

            independent_input()
            native.wait_for(
                lambda: state()["presence"] == "at_desk",
                "Real input did not establish at-desk state",
                15,
            )
            report["active"] = {"state": state(), "oracle": oracle()}
            assert report["active"]["state"]["pulse"]
            ImageGrab.grab(all_screens=True).save(output / "at-desk.png")
            publish()
            native.wait_for(
                lambda: state()["presence"] == "away",
                "Real idle interval did not establish away state",
                50,
            )
            native.wait_for(
                lambda: state()["away"] > report["active"]["state"]["away"] + 5,
                "Away counter did not receive a normal poll",
                15,
            )
            report["away"] = {"state": state(), "oracle": oracle()}
            assert not report["away"]["state"]["pulse"]
            assert report["away"]["state"]["at_desk"] > 0
            assert report["away"]["oracle"]["backend_seconds"] >= 30
            ImageGrab.grab(all_screens=True).save(output / "away.png")
            publish()
            independent_input()
            native.wait_for(
                lambda: state()["presence"] == "at_desk",
                "Independent input did not restore at-desk state",
                15,
            )
            report["reactivated"] = {"state": state(), "oracle": oracle()}
            assert report["reactivated"]["state"]["pulse"]
            assert report["reactivated"]["state"]["away"] > 0
            ImageGrab.grab(all_screens=True).save(output / "reactivated.png")

            def point():
                frame = shared["ui"].window.geometry.build_frame(main_cursor=-1.0)
                geometry = next(
                    g
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://deskpresence"
                )
                bounds = next(
                    w["rect"]
                    for w in native.windows(os.getpid())
                    if w["hwnd"] == dock["hwnd"]
                )
                rect = geometry.draw_rect
                return bounds[0] + rect.x + rect.w / 2, bounds[1] + rect.y + rect.h / 2

            native.click(*call(point), right=True)
            menu = native.wait_for(
                lambda: call(
                    lambda: (
                        value
                        if isinstance(value := Gtk.grab_get_current(), Gtk.Menu)
                        else None
                    )
                ),
                "Desk Presence menu did not open",
            )
            report["menu_labels"] = call(
                lambda: [
                    row.get_label() for row in menu.get_children() if row.get_label()
                ]
            )
            assert "At desk" in report["menu_labels"]
            native.key(0x24)
            native.key(0x27)
            ImageGrab.grab(all_screens=True).save(output / "idle-threshold-menu.png")
            native.key(0x24)
            native.key(0x0D)
            native.wait_for(
                lambda: call(lambda: applet._state.idle_threshold_s) == 60,
                "Native threshold selection did not persist",
            )
            report["threshold_selected"] = 60
            native.key(0x1B)
            # Construction and sampling must not load the Unix idle transport.
            assert not any(
                n.startswith("docking.platform.backends.x11") for n in sys.modules
            )
            report["no_x11_imports"] = True
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
        finally:
            if dock is not None:
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            elif ready.is_set():
                call(lambda: shared["ui"].window.close())
            publish()

    driver = threading.Thread(target=drive, name="windows-idle-smoke", daemon=False)
    driver.start()
    try:
        app.main()
        driver.join(30)
        assert not driver.is_alive()
        service = shared["backend"].idle
        assert service.idle_seconds() is None
        report["stopped_reader_rejected"] = True
        service.start()
        assert service.idle_seconds() is not None
        service.stop()
        assert service.idle_seconds() is None
        report["reader_restart"] = True
        report["workarea_after"] = native.workarea()
        assert native.workarea() == baseline
        report["persisted"] = json.loads(config.read_text())["applet_prefs"][
            "deskpresence"
        ]
        if not errors:
            assert report["persisted"]["idle_threshold_s"] == 60
        report["normal_shutdown"] = True
        if errors:
            raise errors[0]
    except Exception as exc:
        report.update(ok=False, error=repr(exc))
        raise
    finally:
        publish()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
