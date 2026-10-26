"""Exercise Color Picker on real Windows pixels, input, clipboard and menus.

The owned fixture is a separate GTK process with six known color patches.
The complete dock uses its ordinary shared overlay and native screen service.
Run on an otherwise idle desktop; --fixture is an internal child-process mode.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

COLORS = (
    (18, 52, 86),
    (250, 129, 7),
    (0, 255, 0),
    (255, 255, 255),
    (0, 0, 0),
    (129, 7, 250),
)


def fixture(output):
    from gi.repository import Gdk, Gtk

    window = Gtk.Window(title="Docking color fixture")
    window.set_decorated(False)
    screen = Gdk.Screen.get_default()
    window.set_default_size(
        min(660, screen.get_width() - 100), min(360, screen.get_height() - 140)
    )
    window.move(50, 40)
    area = Gtk.DrawingArea()
    clicks = []

    def draw(widget, cr):
        width, height = widget.get_allocated_width(), widget.get_allocated_height()
        for i, rgb in enumerate(COLORS):
            cr.set_source_rgb(*(channel / 255 for channel in rgb))
            cr.rectangle(i % 3 * width / 3, i // 3 * height / 2, width / 3, height / 2)
            cr.fill()
        return False

    def click(widget, event):
        clicks.append([event.x, event.y])
        (output / "fixture-clicks.json").write_text(json.dumps(clicks))
        return True

    (output / "fixture-clicks.json").write_text("[]")
    area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
    area.connect("button-press-event", click)
    area.connect("draw", draw)
    window.add(area)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--shutdown-overlay", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires an owned interactive Windows desktop")
    output = args.output.resolve()
    if not args.fixture:
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
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk

    if args.fixture:
        fixture(output)
        return
    from PIL import ImageGrab

    import docking.app as app
    from docking.platform.backends.windows.ffi import bind
    from docking.platform.backends.windows.screen_capture import (
        WindowsScreenCaptureService,
    )
    from tools.windows_desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline = native.workarea()
    report, shared, errors = {"pid": os.getpid(), "picks": []}, {}, []
    ready = threading.Event()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://colorpicker"},
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
        shared.update(ui=ui, model=kwargs["model"], backend=kwargs["session_backend"])
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
        assert done.wait(30), "GTK did not service an observation"
        if failures:
            raise failures[0]
        return values[0]

    def publish():
        (output / "result.json").write_text(json.dumps(report, indent=2))

    def clipboard():
        value = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-Clipboard -Raw",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return value.stdout.strip()

    def drive():
        dock = child = source = None
        try:
            assert ready.wait(90)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock did not map",
            )
            applet = call(lambda: shared["model"].get_applet("applet://colorpicker"))
            shared["applet"] = applet
            service = shared["backend"].screen_capture
            assert isinstance(service, WindowsScreenCaptureService)
            assert shared["backend"].capabilities.supports_screen_color_pick
            assert call(lambda: applet._screen_capture is service)
            child = subprocess.Popen(
                [sys.executable, __file__, "--output", str(output), "--fixture"],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            source = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows(child.pid)
                        if w["title"] == "Docking color fixture"
                    ),
                    None,
                ),
                "Owned color fixture did not map",
                30,
            )
            report.update(
                fixture=source,
                gtk_scale=call(lambda: shared["ui"].window.get_scale_factor()),
            )
            assert child.pid != os.getpid()
            left, top, right, bottom = source["rect"]
            native.click(left + (right - left) / 6, top + (bottom - top) / 4)
            native.wait_for(
                lambda: native.user.GetForegroundWindow() == source["hwnd"],
                "Owned fixture did not receive foreground before picking",
            )
            native.wait_for(
                lambda: (
                    len(json.loads((output / "fixture-clicks.json").read_text())) == 1
                ),
                "Fixture did not record the verifier's initial focusing click",
            )

            def icon():
                window = shared["ui"].window
                frame = window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://colorpicker"
                )
                bounds = next(
                    w["rect"]
                    for w in native.windows(os.getpid())
                    if w["hwnd"] == dock["hwnd"]
                )
                scale = window.get_scale_factor()
                return (
                    bounds[0] + (rect.x + rect.w / 2) * scale,
                    bounds[1] + (rect.y + rect.h / 2) * scale,
                )

            def focus_state():
                session = shared["ui"].window.surface_service._menus
                return {
                    "foreground": session._native.foreground(),
                    "parent": session._parent_identity,
                    "previous": session._previous,
                    "last": session._last_foreground,
                    "active": len(session._active),
                    "pending": session._finish_source,
                }

            report["initial_focus"] = call(focus_state)

            def begin():
                native.click(*call(icon))
                native.wait_for(
                    lambda: call(lambda: applet._overlay is not None),
                    "Shared capture overlay did not open",
                )

            for i, rgb in enumerate(COLORS):
                left, top, right, bottom = source["rect"]
                point = (
                    int(left + (i % 3 + 0.5) * (right - left) / 3),
                    int(top + (i // 3 + 0.5) * (bottom - top) / 2),
                )
                native.wait_for(
                    lambda expected=rgb, p=point: (
                        ImageGrab.grab(all_screens=True).getpixel(p)[:3] == expected
                    ),
                    "Independent desktop pixels did not match fixture",
                )
                expected = "#" + "".join(f"{c:02X}" for c in rgb)
                begin()
                if i == 0:
                    ImageGrab.grab(all_screens=True).save(
                        output / "capture-overlay.png"
                    )
                native.click(*point)
                native.wait_for(
                    lambda wanted=expected: call(
                        lambda: applet._overlay is None and applet._hex == wanted
                    ),
                    "Actual overlay selection did not sample " + expected,
                    10,
                )
                assert clipboard() == expected
                native.wait_for(
                    lambda: native.user.GetForegroundWindow() == source["hwnd"],
                    "Picking did not restore source foreground",
                    5,
                )
                prefs = json.loads(config.read_text())["applet_prefs"]["colorpicker"]
                assert prefs["hex"] == expected
                report["picks"].append(
                    {
                        "expected": expected,
                        "point": point,
                        "tooltip": call(lambda: applet.item.name),
                        "clipboard": expected,
                        "prefs": prefs,
                        "focus": call(focus_state),
                    }
                )
                publish()
            assert len(json.loads((output / "fixture-clicks.json").read_text())) == 1
            report["no_click_through"] = True
            begin()
            native.key(0x1B)
            native.wait_for(
                lambda: call(lambda: applet._overlay is None),
                "Escape did not cancel the shared overlay",
                5,
            )
            assert call(lambda: applet._hex) == "#8107FA"
            assert clipboard() == "#8107FA"
            report["escape_cancelled"] = True
            native.wait_for(
                lambda: native.user.GetForegroundWindow() == source["hwnd"],
                "Cancellation did not restore source foreground",
                5,
            )
            report["foreground_after_cancel"] = int(native.user.GetForegroundWindow())
            report["focus_after_cancel"] = call(focus_state)
            assert report["foreground_after_cancel"] == source["hwnd"]
            native.click(*call(icon), right=True)
            menu = native.wait_for(
                lambda: call(Gtk.grab_get_current), "Color Picker menu did not map"
            )
            labels = call(
                lambda: [r.get_label() for r in menu.get_children() if r.get_label()]
            )
            assert "Copy #8107FA" in labels and "Show Hex" in labels
            report["menu_labels"] = labels
            ImageGrab.grab(all_screens=True).save(output / "color-picker-menu.png")
            native.key(0x24)
            native.key(0x0D)
            native.wait_for(
                lambda: call(Gtk.grab_get_current) is None,
                "Native Copy menu action did not finish",
            )
            assert clipboard() == "#8107FA"
            native.click(*call(icon), right=True)
            native.wait_for(
                lambda: call(Gtk.grab_get_current), "Toggle menu did not map"
            )
            native.key(0x24)
            native.key(0x28)
            native.key(0x0D)
            native.wait_for(
                lambda: call(lambda: not applet._show_hex),
                "Native Show Hex selection did not update the applet",
            )
            assert not json.loads(config.read_text())["applet_prefs"]["colorpicker"][
                "show_hex"
            ]
            report["show_hex_toggled"] = True
            ImageGrab.grab(all_screens=True).save(output / "hex-label-hidden.png")
            # Repeated real sampling must not retain native GDI device contexts.
            kernel = ctypes.WinDLL("kernel32")
            process = bind(kernel, "GetCurrentProcess", ctypes.c_void_p)()
            resources = bind(
                native.user,
                "GetGuiResources",
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_uint32,
            )

            def repeated_reads():
                display = Gdk.Display.get_default()
                monitor = display.get_monitor(0)
                bounds, scale = monitor.get_geometry(), monitor.get_scale_factor()
                x = (
                    bounds.x
                    + (source["rect"][0] + (source["rect"][2] - source["rect"][0]) / 6)
                    / scale
                )
                y = (
                    bounds.y
                    + (source["rect"][1] + (source["rect"][3] - source["rect"][1]) / 4)
                    / scale
                )
                service.pick_color(x=int(x), y=int(y))
                before = resources(process, 0)
                for _ in range(300):
                    assert service.pick_color(x=int(x), y=int(y)) == COLORS[0]
                return {"before": before, "after": resources(process, 0), "reads": 300}

            report["gdi"] = call(repeated_reads)
            assert report["gdi"]["after"] <= report["gdi"]["before"]
            assert not any(
                n.startswith("docking.platform.backends.x11") for n in sys.modules
            )
            report.update(ok=True, no_x11_imports=True)
            if args.shutdown_overlay:
                begin()
                report["active_overlay_at_shutdown"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            if args.shutdown_overlay and report.get("ok") and dock is not None:
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
                try:
                    native.wait_for(
                        lambda: not native.user.IsWindow(dock["hwnd"]),
                        "Native close did not dismiss capture and close the dock",
                        20,
                    )
                    report["native_close_dismissed_capture"] = True
                except Exception as exc:
                    errors.append(exc)
                    report.update(ok=False, close_error=repr(exc))
            if (
                ready.is_set()
                and "applet" in shared
                and (dock is None or native.user.IsWindow(dock["hwnd"]))
            ):
                call(lambda: shared["applet"]._dismiss_overlay())
            if source is not None:
                native.user.PostMessageW(source["hwnd"], 0x10, 0, 0)
            if child is not None:
                try:
                    report["fixture_exit"] = child.wait(20)
                    assert child.returncode == 0
                except Exception as exc:
                    errors.append(exc)
                    report.update(ok=False, fixture_cleanup_error=repr(exc))
            if dock is not None:
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            elif ready.is_set():
                call(lambda: shared["ui"].window.close())
            publish()

    driver = threading.Thread(
        target=drive, name="windows-colorpicker-smoke", daemon=False
    )
    driver.start()
    try:
        app.main()
        driver.join(30)
        assert not driver.is_alive()
        assert shared["backend"].screen_capture.pick_color(x=0, y=0) is None
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
