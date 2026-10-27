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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check-visibility",
        action="store_true",
        help="Check the installed Windows Clock's hidden widget in menus and Search",
    )
    mode.add_argument(
        "--classic",
        action="store_true",
        help="Exercise native Character Map Search, drag-to-pin and activation",
    )
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
                "global_search_enabled": True,
            }
        ),
        encoding="utf-8",
    )
    native = WindowsDesktop()
    app_name = "Character Map" if args.classic else "Notepad"
    expected_id = (
        r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\charmap.exe"
        if args.classic
        else "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"
    )

    def matches_application(window):
        return (
            window["title"] == app_name
            if args.classic
            else window["class"] == "Notepad"
        )

    assert not any(w["title"] == "Docking" for w in native.windows())
    assert not any(matches_application(w) for w in native.windows()), (
        "Close the lab's existing application before running this check"
    )
    baseline = native.workarea()
    ready = threading.Event()
    report, shared, errors = {"baseline_workarea": baseline}, {}, []
    blocked, release = threading.Event(), threading.Event()
    main_id = "Microsoft.WindowsAlarms_8wekyb3d8bbwe!App"
    widget_id = "Microsoft.WindowsAlarms_8wekyb3d8bbwe!ClockWidgets"
    if args.check_visibility:
        from docking.platform.backends.windows import packages

        original_resolve = packages.hidden_application_ids

        def resolve(ids):
            # This fixture delays real OS metadata; it never replaces the result.
            if widget_id in ids and not release.is_set():
                blocked.set()
                assert release.wait(90), "Visibility fixture was not released"
            result = original_resolve(ids)
            report["native_hidden_ids"] = sorted(result)
            return result

        packages.hidden_application_ids = resolve
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
        shared.update(ui=ui, model=kwargs["model"], kwargs=kwargs)
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

    def label(widget):
        if isinstance(widget, Gtk.Label):
            return widget.get_text()
        if isinstance(widget, Gtk.Container):
            return next(
                (value for child in widget.get_children() if (value := label(child))),
                "",
            )
        return ""

    def check_visibility(ui, menu, entry, point):
        assert blocked.wait(10), "No real package lookup reached the fixture"
        registry = shared["kwargs"]["application_registry"]
        title = call(lambda: registry.get(main_id).name)
        assert call(lambda: registry.get(widget_id).name) == title
        native.type_text(title)
        native.wait_for(lambda: call(entry.get_text) == title, "Menu input was lost")

        def matching_rows():
            return [
                row
                for row in menu.get_children()
                if row.get_visible() and label(row) == title
            ]

        assert call(lambda: len(matching_rows())) == 2
        capture("visibility-menu-pending")
        native.key(0x1B)
        native.click(1100, 100)
        native.key(0x20, control=True, alt=True)
        native.wait_for(
            lambda: any(
                w["title"] == "Docking Search" for w in native.windows(os.getpid())
            ),
            "Native shortcut did not open Search",
        )
        native.type_text("clock")

        def search_state():
            window = ui.search.window
            ids = [
                dict(row.result.metadata).get("desktop_id")
                for row in window.results_list.get_children()
                if row.result.identity.provider_id == "applications"
            ]
            selected = window.selected_result()
            return {
                "ids": ids,
                "query": window.search_entry.get_text(),
                "focus": window.window.get_focus() is window.search_entry,
                "selection": str(selected.identity) if selected else None,
                "registry_generation": registry.generation,
            }

        native.wait_for(
            lambda: {main_id, widget_id} <= set(call(search_state)["ids"]),
            "Search did not expose both raw Gio entries before native metadata",
        )
        before = call(search_state)
        capture("visibility-search-pending")
        release.set()
        native.wait_for(
            lambda: widget_id not in call(search_state)["ids"],
            "Open Search did not remove the hidden component",
        )
        after = call(search_state)
        assert main_id in after["ids"] and after["ids"].count(main_id) == 1
        assert after["query"] == before["query"] == "clock"
        assert after["focus"] and before["focus"]
        assert after["selection"] == before["selection"]
        assert after["registry_generation"] > before["registry_generation"]
        assert call(lambda: registry.get(widget_id).visible) is False
        for app_id in (main_id, widget_id):
            assert (
                call(lambda app_id=app_id: registry._gio_handle_for(app_id).get_id())
                == app_id
            )
        report["visibility_search"] = {"before": before, "after": after}
        capture("visibility-search-ready")
        title = call(lambda: registry.get(main_id).name)
        native.key(0x1B)
        native.click(1100, 100)
        native.click(*call(point))
        applet = call(lambda: shared["model"].get_applet("applet://applications"))
        menu = native.wait_for(
            lambda: call(lambda: applet._popup_menu), "Applications did not reopen"
        )
        entry = call(lambda: menu.get_children()[0].get_child().get_children()[0])
        native.type_text(title)
        native.wait_for(lambda: call(entry.get_text) == title, "Menu input was lost")
        assert call(lambda: len(matching_rows())) == 1
        capture("visibility-menu-ready")

        def row_point():
            row = matching_rows()[0]
            origin = menu.get_window().get_origin()
            offset = row.translate_coordinates(
                menu, row.get_allocated_width() // 2, row.get_allocated_height() // 2
            )
            assert offset is not None
            return origin[-2] + offset[0], origin[-1] + offset[1]

        windows = shared["kwargs"]["window_tracker"]
        assert not call(lambda: tuple(windows.list_windows(main_id))), (
            "Close the lab's existing Clock before running this check"
        )
        native.click(*call(row_point))
        launched = native.wait_for(
            lambda: call(lambda: next(iter(windows.list_windows(main_id)), None)),
            "The visible Clock row did not launch the real application",
            30,
        )
        shared["launched_hwnd"] = launched.id.value
        assert launched.app_id == main_id
        assert not call(lambda: tuple(windows.list_windows(widget_id)))
        native.wait_for(
            lambda: native.user.GetForegroundWindow() == launched.id.value,
            "The visible Clock row did not activate the real application",
        )
        report["visibility_launch"] = {
            "hwnd": launched.id.value,
            "app_id": launched.app_id,
            "title": launched.title,
        }
        time.sleep(2)
        capture("visibility-clock-launched")
        assert native.user.PostMessageW(launched.id.value, 0x10, 0, 0)
        native.wait_for(
            lambda: not native.user.IsWindow(launched.id.value),
            "Owned Clock did not close",
        )
        assert native.user.PostMessageW(shared["dock_hwnd"], 0x10, 0, 0)
        native.wait_for(
            lambda: not native.user.IsWindow(shared["dock_hwnd"]),
            "Docking did not close normally",
        )
        report["ok"] = True

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
            if args.classic:
                native.key(0x20, control=True, alt=True)
                search = native.wait_for(
                    lambda: next(
                        (
                            w
                            for w in native.windows(os.getpid())
                            if w["title"] == "Docking Search"
                        ),
                        None,
                    ),
                    "Native shortcut did not open Search",
                )
                native.wait_for(
                    lambda: native.user.GetForegroundWindow() == search["hwnd"],
                    "Search did not acquire foreground",
                )
                native.type_text(app_name)

                def search_ready():
                    cache = shared["kwargs"]["icon_loader"]._windows_icons
                    if cache is None or cache._images.get((expected_id, 32)) is None:
                        return False
                    image = cache._images[(expected_id, 32)]
                    return any(
                        dict(row.result.metadata).get("desktop_id") == expected_id
                        and row.image.get_pixbuf() is not None
                        and bytes(row.image.get_pixbuf().get_pixels()) == image.pixels
                        for row in ui.search.window.results_list.get_children()
                        if row.result.identity.provider_id == "applications"
                    )

                native.wait_for(
                    lambda: call(search_ready),
                    "Classic Search result did not receive native shell artwork",
                )
                report["classic_search"] = {
                    "id": expected_id,
                    "query": app_name,
                    "native_artwork": True,
                }
                capture("classic-search")
                native.key(0x1B)
                native.click(1100, 100)
            native.click(*call(point))
            menu = native.wait_for(
                lambda: call(lambda: applet._popup_menu),
                "Applications menu did not open",
            )
            entry = call(lambda: menu.get_children()[0].get_child().get_children()[0])
            assert isinstance(entry, Gtk.Entry)
            if args.check_visibility:
                check_visibility(ui, menu, entry, point)
                return
            report["menu_foreground"] = native.user.GetForegroundWindow()
            native.type_text(app_name)
            native.wait_for(
                lambda: call(entry.get_text) == app_name,
                "Native typing did not reach the application filter",
                5,
            )
            report["typed_filter"] = call(entry.get_text)

            report["visible_labels"] = call(
                lambda: [
                    label(w)
                    for w in menu.get_children()
                    if w.get_visible() and label(w)
                ]
            )
            assert app_name in report["visible_labels"]
            time.sleep(0.5)
            capture("applications-filter")
            application = call(lambda: model._application_registry.get(expected_id))
            assert application is not None and application.desktop_file is None
            report["application"] = {"id": application.desktop_id, "desktop_file": None}
            row = call(
                lambda: next(
                    w
                    for w in menu.get_children()
                    if w.get_visible() and label(w) == app_name
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
            assert not any(matches_application(w) for w in native.windows()), (
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
                    (w for w in native.windows() if matches_application(w)), None
                ),
                "The newly pinned launcher did not open the application",
                30,
            )
            shared["launched_hwnd"] = launched["hwnd"]
            launched = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows()
                        if w["hwnd"] == shared["launched_hwnd"]
                        and w["title"]
                        and (args.classic or w["title"] != "Notepad")
                    ),
                    None,
                ),
                "The application did not finish loading its window",
                30,
            )
            native.wait_for(
                lambda: native.user.GetForegroundWindow() == launched["hwnd"],
                "The newly pinned launcher did not activate the application",
            )
            report["launched_window"] = launched
            if args.classic:
                windows = shared["kwargs"]["window_tracker"]
                grouped = native.wait_for(
                    lambda: call(lambda: tuple(windows.list_windows(expected_id))),
                    "Classic window did not group under its pinned canonical ID",
                )
                assert any(w.id.value == launched["hwnd"] for w in grouped)
                report["classic_grouping"] = [
                    {"hwnd": w.id.value, "desktop_id": w.desktop_id, "app_id": w.app_id}
                    for w in grouped
                ]
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
            release.set()
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
