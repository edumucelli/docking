"""Exercise the Session applet using actual Windows desktop input.

Default mode inspects menus and shuts the dock down normally. --action lock
waits for an external operator to unlock the owned test VM. Logout/reboot/
poweroff end the guest session: their effect must be verified externally and
after recovery, not inferred from an accepted API request. Use only in a test VM.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from pathlib import Path


def session_state():
    """Independent WTS session state; no account names or credentials reported."""
    from docking.platform.native.windows.ffi import bind

    class Level1(ctypes.Structure):
        _fields_ = [
            ("session", ctypes.c_uint32),
            ("state", ctypes.c_int32),
            ("flags", ctypes.c_int32),
            ("station", ctypes.c_wchar * 33),
            ("user", ctypes.c_wchar * 21),
            ("domain", ctypes.c_wchar * 18),
            ("times", ctypes.c_int64 * 5),
            ("traffic", ctypes.c_uint32 * 6),
        ]

    class Info(ctypes.Structure):
        _fields_ = [("level", ctypes.c_uint32), ("data", Level1)]

    wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
    query = bind(
        wts,
        "WTSQuerySessionInformationW",
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_uint32),
    )
    free = bind(wts, "WTSFreeMemory", None, ctypes.c_void_p)
    pointer, size = ctypes.c_void_p(), ctypes.c_uint32()
    assert query(None, 0xFFFFFFFF, 25, ctypes.byref(pointer), ctypes.byref(size))
    try:
        assert size.value >= ctypes.sizeof(Info)
        value = Info.from_buffer_copy(ctypes.string_at(pointer, ctypes.sizeof(Info)))
        assert value.level == 1
        return {
            "session": value.data.session,
            "state": value.data.state,
            "flags": value.data.flags,
            "logon_time": value.data.times[0],
        }
    finally:
        free(pointer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--action",
        choices=("inspect", "lock", "logout", "reboot", "poweroff"),
        default="inspect",
    )
    parser.add_argument("--lock-source", choices=("click", "menu"), default="click")
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
    from docking.applets.session.implementations.windows.backend import (
        WindowsSessionBackend,
    )
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
                    {"kind": "applet", "target": "applet://session"},
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
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline = native.workarea()
    report = {
        "action": args.action,
        "pid": os.getpid(),
        "baseline_workarea": baseline,
        "session_before": session_state(),
    }
    assert report["session_before"]["flags"] == 1
    errors, shared = [], {}
    ready = threading.Event()
    original = app.build_dock_window

    def build(**kwargs):
        ui = original(**kwargs)
        shared.update(ui=ui, model=kwargs["model"])
        ready.set()
        return ui

    app.build_dock_window = build

    def publish(phase):
        report["phase"] = phase
        temporary = output / "result.tmp"
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(output / "result.json")

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
        assert done.wait(20), "GTK did not service the desktop observation"
        if failures:
            raise failures[0]
        return values[0]

    def drive():
        ending_session = False
        try:
            assert ready.wait(90)
            applet = call(lambda: shared["model"].get_applet("applet://session"))
            assert isinstance(applet._backend, WindowsSessionBackend)
            shared["applet"] = applet
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
                    if g.item.desktop_id == "applet://session"
                )
                window = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                return window["rect"][0] + rect.x + rect.w / 2, window["rect"][
                    1
                ] + rect.y + rect.h / 2

            time.sleep(2)
            native.click(*call(point), right=True)
            menu = native.wait_for(
                lambda: call(
                    lambda: (
                        g if isinstance(g := Gtk.grab_get_current(), Gtk.Menu) else None
                    )
                ),
                "Session menu did not open",
            )
            rows = call(
                lambda: [
                    {"label": r.get_label(), "enabled": r.get_sensitive()}
                    for r in menu.get_children()
                    if r.get_label()
                ]
            )
            available = call(applet._backend.available_actions)
            report.update(menu=rows, available=sorted(available))
            actions = ["lock", "suspend", "logout", "reboot", "poweroff"]
            assert [r["label"] for r in rows[:5]] == [
                "Lock Screen",
                "Suspend",
                "Log Out",
                "Restart",
                "Shut Down",
            ]
            assert all(
                row["enabled"] == (action in available)
                for row, action in zip(rows, actions, strict=False)
            )
            time.sleep(0.5)
            ImageGrab.grab(all_screens=True).save(output / "session-menu.png")
            publish("menu-verified")
            native.key(0x1B)
            if args.action == "inspect":
                report["ok"] = True
                return
            assert args.action in available
            report["issued_at"] = time.time()
            publish("issuing-action")
            if args.action == "lock" and args.lock_source == "click":
                native.click(*call(point))
            else:
                native.click(*call(point), right=True)
                native.wait_for(
                    lambda: call(lambda: isinstance(Gtk.grab_get_current(), Gtk.Menu)),
                    "Session menu did not reopen",
                )
                enabled = [a for a in actions if a in available]
                native.key(0x24)
                for _ in range(enabled.index(args.action)):
                    native.key(0x28)
                native.key(0x0D)
            if args.action == "lock":
                state = native.wait_for(
                    lambda: s if (s := session_state())["flags"] == 0 else None,
                    "Session did not lock",
                    20,
                )
                report["session_locked"] = state
                publish("locked-awaiting-external-unlock")
                state = native.wait_for(
                    lambda: s if (s := session_state())["flags"] == 1 else None,
                    "External unlock was not observed",
                    240,
                )
                assert state["logon_time"] == report["session_before"]["logon_time"]
                report["session_unlocked"] = state
                report["ok"] = True
            else:
                # OS shutdown/logoff terminates this process and the guest agent.
                # A host observer must verify the transition and recover the VM.
                ending_session = True
                publish("awaiting-external-transition-verification")
                native.wait_for(
                    lambda: False,
                    "No external session transition within 90 seconds",
                    90,
                )
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            publish("failed")
        finally:
            if ready.is_set() and (not ending_session or errors):

                def close():
                    grab = Gtk.grab_get_current()
                    if isinstance(grab, Gtk.MenuShell):
                        grab.cancel()
                    shared["ui"].window.close()

                call(close)

    driver = threading.Thread(target=drive, name="windows-session-smoke", daemon=True)
    driver.start()
    try:
        app.main()
        driver.join(15)
        assert not driver.is_alive()
        assert native.workarea() == baseline
        binding = WindowsHotkeyAPI()
        binding.register(MOD_CONTROL | MOD_ALT, 0x20)
        binding.unregister()
        assert shared["applet"]._backend._stopped
        report["normal_shutdown"] = True
        if errors:
            raise errors[0]
    finally:
        publish("finished")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
