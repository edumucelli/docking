"""Exercise the real Applications menu with native Windows keyboard and mouse."""

from __future__ import annotations

import argparse
import json
import os
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
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.platform.backends.windows.hotkeys import (
        MOD_ALT,
        MOD_CONTROL,
        WindowsHotkeyAPI,
    )
    from docking.ui.dnd import DnDHandler
    from tools.windows.desktop_driver import WindowsDesktop

    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://applications"},
                    {"kind": "applet", "target": "applet://clock"},
                ],
                "icon_size": 48,
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
    assert not any(w["class"] == "Notepad" for w in native.windows()), (
        "Close the lab's existing Notepad before running this check"
    )
    baseline = native.workarea()
    ready = threading.Event()
    report, shared, errors = {"baseline_workarea": baseline}, {}, []
    original_received = DnDHandler._on_drag_data_received

    def observed_received(handler, widget, context, x, y, selection, info, time):
        report.setdefault("drops", []).append(
            {
                "protocol": context.get_protocol().value_nick,
                "info": info,
                "data": (selection.get_data() or b"").decode("utf-8", errors="replace"),
            }
        )
        return original_received(handler, widget, context, x, y, selection, info, time)

    DnDHandler._on_drag_data_received = observed_received
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
        try:
            assert ready.wait(90)
            ui, model = shared["ui"], shared["model"]
            applet = call(lambda: model.get_applet("applet://applications"))
            assert applet is not None
            dock = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows(os.getpid())
                        if w["title"] == "Docking" and w["class"] == "gdkWindowToplevel"
                    ),
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
                    if g.item.desktop_id == "applet://applications"
                )
                window = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                return window["rect"][0] + rect.x + rect.w / 2, window["rect"][
                    1
                ] + rect.y + rect.h / 2

            time.sleep(2)
            native.click(1100, 100)
            native.click(*call(point))
            menu = native.wait_for(
                lambda: call(lambda: applet._popup_menu),
                "Applications menu did not open",
            )
            entry = call(lambda: menu.get_children()[0].get_child().get_children()[0])
            assert isinstance(entry, Gtk.Entry)
            report["menu_foreground"] = native.user.GetForegroundWindow()
            native.type_text("Notepad")
            native.wait_for(
                lambda: call(entry.get_text) == "Notepad",
                "Native typing did not reach the application filter",
                5,
            )
            report["typed_filter"] = call(entry.get_text)

            def label(widget):
                if isinstance(widget, Gtk.Label):
                    return widget.get_text()
                if isinstance(widget, Gtk.Container):
                    return next(
                        (
                            value
                            for child in widget.get_children()
                            if (value := label(child))
                        ),
                        "",
                    )
                return ""

            report["visible_labels"] = call(
                lambda: [
                    label(w)
                    for w in menu.get_children()
                    if w.get_visible() and label(w)
                ]
            )
            assert "Notepad" in report["visible_labels"]
            time.sleep(0.5)
            capture("applications-filter")
            expected_id = "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"
            application = call(lambda: model._application_registry.get(expected_id))
            assert application is not None and application.desktop_file is None
            report["application"] = {"id": application.desktop_id, "desktop_file": None}
            row = call(
                lambda: next(
                    w
                    for w in menu.get_children()
                    if w.get_visible() and label(w) == "Notepad"
                )
            )
            drag_ended = threading.Event()

            def drag_begin(_row, context):
                report["drag_source"] = {
                    "protocol": context.get_protocol().value_nick,
                    "targets": [target.name() for target in context.list_targets()],
                }

            call(lambda: row.connect("drag-begin", drag_begin))
            call(lambda: row.connect("drag-end", lambda *_args: drag_ended.set()))

            def row_point():
                origin = menu.get_window().get_origin()
                offset = row.translate_coordinates(
                    menu,
                    row.get_allocated_width() // 2,
                    row.get_allocated_height() // 2,
                )
                assert offset is not None
                return origin[-2] + offset[0], origin[-1] + offset[1]

            sx, sy = call(row_point)
            frame = call(lambda: ui.window.geometry.build_frame(main_cursor=-1.0))
            rect = frame.item_geometries[-1].draw_rect
            current = next(
                w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
            )
            tx, ty = (
                current["rect"][0] + rect.x + rect.w / 2,
                current["rect"][1] + rect.y + rect.h - 3,
            )
            report["drag_points"] = {"source": [sx, sy], "destination": [tx, ty]}
            native.user.SetCursorPos(int(sx), int(sy))
            time.sleep(0.3)
            native.user.mouse_event(2, 0, 0, 0, 0)
            try:
                for step in range(1, 31):
                    native.user.SetCursorPos(
                        int(sx + (tx - sx) * step / 30), int(sy + (ty - sy) * step / 30)
                    )
                    time.sleep(0.05)
                time.sleep(0.5)
                capture("application-dragging")
            finally:
                native.user.mouse_event(4, 0, 0, 0, 0)
            assert drag_ended.wait(15), "Application row drag did not end"
            native.wait_for(
                lambda: any(
                    p["target"] == expected_id
                    for p in json.loads(config.read_text(encoding="utf-8"))["pinned"]
                ),
                "Application row drag did not pin the native ID",
            )
            assert (
                report["drops"][-1]["info"] == 2
                and report["drops"][-1]["data"] == expected_id
            )
            assert report["drops"][-1]["protocol"] == "ole2"
            native.key(0x1B)
            time.sleep(0.7)
            capture("application-pinned")
            assert not any(w["class"] == "Notepad" for w in native.windows()), (
                "Dragging activated the application"
            )
            report["native_pin"] = True

            # A drop must create the same usable launcher as a regular pin.
            def application_point():
                frame = ui.window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == expected_id
                )
                window = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                return (
                    window["rect"][0] + rect.x + rect.w / 2,
                    window["rect"][1] + rect.y + rect.h / 2,
                )

            native.click(1100, 100)
            native.click(*call(application_point))
            launched = native.wait_for(
                lambda: next(
                    (w for w in native.windows() if w["class"] == "Notepad"), None
                ),
                "The newly pinned launcher did not open Notepad",
                30,
            )
            shared["launched_hwnd"] = launched["hwnd"]
            launched = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows()
                        if w["hwnd"] == shared["launched_hwnd"]
                        and w["title"] not in {"", "Notepad"}
                    ),
                    None,
                ),
                "Notepad did not finish loading its document",
                30,
            )
            native.wait_for(
                lambda: native.user.GetForegroundWindow() == launched["hwnd"],
                "The newly pinned launcher did not activate Notepad",
            )
            report["launched_window"] = launched
            time.sleep(2)
            capture("application-launched")
            assert native.user.PostMessageW(launched["hwnd"], 0x10, 0, 0)
            native.wait_for(
                lambda: not native.user.IsWindow(launched["hwnd"]),
                "The test's newly opened Notepad window did not close",
            )
            time.sleep(0.7)
            # Open the menu again so native close also covers an active grab.
            native.click(1100, 100)
            native.click(*call(point))
            native.wait_for(
                lambda: call(lambda: applet._popup_menu),
                "Applications menu did not reopen",
            )
            assert native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            native.wait_for(
                lambda: not native.user.IsWindow(dock["hwnd"]),
                "Native close was blocked by the Applications menu grab",
                5,
            )
            report["native_close_during_menu"] = True
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(
                ok=False, error=repr(exc), foreground=native.user.GetForegroundWindow()
            )
            if "entry" in locals():
                report["actual_filter"] = call(entry.get_text)
            capture("failure")
        finally:
            launched_hwnd = shared.get("launched_hwnd", 0)
            if native.user.IsWindow(launched_hwnd):
                native.user.PostMessageW(launched_hwnd, 0x10, 0, 0)
            if ready.is_set() and native.user.IsWindow(shared.get("dock_hwnd", 0)):

                def close():
                    grab = Gtk.grab_get_current()
                    if isinstance(grab, Gtk.MenuShell):
                        grab.cancel()
                    shared["ui"].window.close()

                call(close)

    driver = threading.Thread(
        target=drive, name="windows-applications-smoke", daemon=True
    )
    driver.start()
    try:
        app.main()
        driver.join(10)
        assert not driver.is_alive()
        assert native.workarea() == baseline
        binding = WindowsHotkeyAPI()
        binding.register(MOD_CONTROL | MOD_ALT, 0x20)
        binding.unregister()
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
