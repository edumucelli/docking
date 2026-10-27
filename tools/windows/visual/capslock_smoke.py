"""Verify real keyboard toggle polling while an owned Notepad has focus.

Uses the normal applet timer and a separate native-input process. Restores the
original lock indicators and closes the owned Notepad window.
Requires the lab's US keyboard layout and an otherwise idle Windows desktop.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import threading
from dataclasses import asdict
from pathlib import Path

ORACLE = r"""
Add-Type -AssemblyName System.Windows.Forms
@{
 caps_lock=[Windows.Forms.Control]::IsKeyLocked([Windows.Forms.Keys]::CapsLock)
 num_lock=[Windows.Forms.Control]::IsKeyLocked([Windows.Forms.Keys]::NumLock)
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
    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.applets.capslock.state import state_label
    from docking.applets.capslock.windows import WindowsLockKeyBackend
    from tools.windows.visual.desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    assert not any(
        w["title"] == "Docking" or "Notepad" in w["title"] for w in native.windows()
    )
    baseline = native.workarea()
    shared, errors, report = {}, [], {"pid": os.getpid(), "states": {}}
    ready = threading.Event()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://capslock"},
                    {"kind": "applet", "target": "applet://clock"},
                ],
                "icon_size": 64,
                "hide_mode": "none",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        )
    )
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

    def publish():
        (output / "result.json").write_text(json.dumps(report, indent=2))

    def oracle():
        value = subprocess.run(
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
        return json.loads(value.stdout.lstrip("\ufeff"))

    def independent_keys(keys):
        program = (
            "import sys,os;sys.path.insert(0," + repr(str(repo)) + ");"
            "from tools.windows.visual.desktop_driver import WindowsDesktop;"
            "n=WindowsDesktop();"
            + ";".join(f"n.key({key})" for key in keys)
            + ";print(os.getpid())"
        )
        result = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        pid = int(result.stdout.strip())
        assert pid != os.getpid()
        report.setdefault("input_processes", []).append(pid)

    def drive():
        dock = editor = before = None
        document = output / "docking-lock-indicators.txt"
        try:
            before = oracle()
            report["baseline"] = before
            assert ready.wait(90)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock did not map",
            )
            applet = call(lambda: shared["model"].get_applet("applet://capslock"))
            assert isinstance(applet._backend, WindowsLockKeyBackend)
            document.write_text("", encoding="utf-8")
            os.startfile(str(document))
            editor = native.wait_for(
                lambda: next(
                    (w for w in native.windows() if document.name in w["title"]), None
                ),
                "Owned Notepad did not map",
                30,
            )
            assert editor["pid"] != os.getpid()
            report["editor"] = editor

            def focus_editor():
                rect = next(
                    w["rect"] for w in native.windows() if w["hwnd"] == editor["hwnd"]
                )
                native.click(rect[0] + 150, rect[1] + 200)
                native.wait_for(
                    lambda: native.user.GetForegroundWindow() == editor["hwnd"],
                    "Notepad did not receive foreground",
                )

            focus_editor()
            for caps, num in (
                (False, False),
                (True, False),
                (True, True),
                (False, True),
            ):
                current = oracle()
                keys = [
                    key
                    for name, key, wanted in (
                        ("caps_lock", 0x14, caps),
                        ("num_lock", 0x90, num),
                    )
                    if current[name] != wanted
                ]
                if keys:
                    independent_keys(keys)
                expected = {"available": True, "caps_lock": caps, "num_lock": num}
                native.wait_for(
                    lambda wanted=expected: (
                        call(lambda: asdict(applet._state)) == wanted
                    ),
                    "Background applet did not observe real lock toggles: "
                    + repr(expected),
                    8,
                )
                value = oracle()
                assert value == {"caps_lock": caps, "num_lock": num}
                assert native.user.GetForegroundWindow() == editor["hwnd"]
                native.key(0x41, control=True)
                native.key(0x08)
                native.scan(0x1E)  # US physical A, with no Unicode injection.
                native.key(0x53, control=True)
                expected_text = "A" if caps else "a"
                native.wait_for(
                    lambda wanted=expected_text: (
                        document.read_text(encoding="utf-8-sig") == wanted
                    ),
                    "Notepad typing did not match Caps Lock",
                    20,
                )
                label = call(lambda: state_label(applet._state))
                report["states"][label] = {
                    "state": call(lambda: asdict(applet._state)),
                    "oracle": value,
                    "text": document.read_text(encoding="utf-8-sig"),
                    "foreground": int(native.user.GetForegroundWindow()),
                    "tooltip": call(lambda: applet.item.name),
                }
                ImageGrab.grab(all_screens=True).save(output / (label + ".png"))
                publish()

            def point():
                frame = shared["ui"].window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://capslock"
                )
                bounds = next(
                    w["rect"]
                    for w in native.windows(os.getpid())
                    if w["hwnd"] == dock["hwnd"]
                )
                return bounds[0] + rect.x + rect.w / 2, bounds[1] + rect.y + rect.h / 2

            native.click(*call(point), right=True)
            menu = native.wait_for(
                lambda: call(Gtk.grab_get_current), "Caps Lock menu did not map"
            )
            labels = call(
                lambda: [r.get_label() for r in menu.get_children() if r.get_label()]
            )
            assert "Caps Lock: Off" in labels and "Num Lock: On" in labels
            report["menu_labels"] = labels
            ImageGrab.grab(all_screens=True).save(output / "lock-menu.png")
            native.key(0x24)
            native.key(0x0D)  # Refresh Now, using the real menu action.
            native.wait_for(
                lambda: call(Gtk.grab_get_current) is None, "Refresh did not close menu"
            )
            assert call(lambda: applet._state.available)
            assert not any(
                n.startswith(
                    ("docking.applets.capslock.linux", "docking.platform.backends.x11")
                )
                for n in sys.modules
            )
            report.update(ok=True, no_unix_imports=True)
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            try:
                if before is not None:
                    current = oracle()
                    keys = [
                        key
                        for name, key in (("caps_lock", 0x14), ("num_lock", 0x90))
                        if current[name] != before[name]
                    ]
                    if keys:
                        independent_keys(keys)
                    assert oracle() == before
                    report["indicators_restored"] = True
                if editor is not None:
                    focus_editor()
                    native.key(0x53, control=True)
                    native.key(0x57, control=True)
                    native.user.PostMessageW(editor["hwnd"], 0x10, 0, 0)
                    native.wait_for(
                        lambda: not native.user.IsWindow(editor["hwnd"]),
                        "Owned Notepad did not close",
                        20,
                    )
                    report["notepad_closed"] = True
            except Exception as exc:
                errors.append(exc)
                report.update(ok=False, cleanup_error=repr(exc))
            if dock is not None:
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            elif ready.is_set():
                call(lambda: shared["ui"].window.close())
            publish()

    driver = threading.Thread(target=drive, name="windows-capslock-smoke", daemon=False)
    driver.start()
    try:
        app.main()
        driver.join(30)
        assert not driver.is_alive()
        report["workarea_after"] = native.workarea()
        assert native.workarea() == baseline
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
