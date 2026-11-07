"""Exercise the real Applications menu with native Windows keyboard and mouse."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--installed-root", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--application-id")
    parser.add_argument("--application-name")
    parser.add_argument("--launcher-source", type=Path)
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
    parser.add_argument(
        "--trace-surfaces",
        action="store_true",
        help="Record native and GTK popup ownership across the drag lifecycle",
    )
    parser.add_argument(
        "--check-artwork",
        action="store_true",
        help="Hold real menu artwork and check the unavailable-capture preview",
    )
    parser.add_argument("--explorer-export", action="store_true")
    parser.add_argument("--export-directory", type=Path)
    finish = parser.add_mutually_exclusive_group()
    finish.add_argument("--cancel-export", action="store_true")
    finish.add_argument("--close-during-export", action="store_true")
    args = parser.parse_args()
    if bool(args.application_id) != bool(args.application_name):
        parser.error(
            "Custom acceptance needs both --application-id and --application-name"
        )
    if args.application_id and (args.classic or args.check_visibility):
        parser.error("Choose the owned application or an installed default fixture")
    if args.launcher_source is not None and not args.application_id:
        parser.error("--launcher-source requires an exact owned application ID")
    args.classic = args.classic or bool(args.application_id)
    if args.check_artwork and args.check_visibility:
        parser.error("Choose artwork or visibility acceptance for this run")
    args.trace_surfaces = args.trace_surfaces or args.check_artwork
    if sys.platform != "win32":
        parser.error("Requires native Windows Python")
    if args.explorer_export and (
        args.export_directory is None or args.check_visibility or args.check_artwork
    ):
        parser.error(
            "Export requires an owned --export-directory and no other check mode"
        )
    if (args.cancel_export or args.close_during_export) and not args.explorer_export:
        parser.error("Export lifecycle checks require --explorer-export")
    output = args.output.resolve()
    if (output / "result.json").exists():
        parser.error("Choose a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    profile = (
        tempfile.TemporaryDirectory(prefix="docking-export-profile-")
        if args.explorer_export
        else None
    )
    profile_root = Path(profile.name) if profile is not None else output
    os.environ["XDG_CONFIG_HOME"] = str(profile_root / "config")
    os.environ["XDG_CACHE_HOME"] = str(profile_root / "cache")
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from tools.windows.visual.tray_acceptance import runtime_origins, select_runtime

    runtime = select_runtime(args.installed_root, args.wheel)
    if args.launcher_source is not None:
        from docking.platform.applications.implementations.windows.registration import (
            WindowsExecutableRegistration,
        )

        assert (
            WindowsExecutableRegistration().create(
                args.launcher_source, startup_wm_class=""
            )
            == args.application_id
        )
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.platform.native.windows.hotkeys import (
        MOD_ALT,
        MOD_CONTROL,
        WindowsHotkeyAPI,
    )
    from docking.ui.dnd import DnDHandler
    from tools.windows.visual.desktop_driver import WindowsDesktop

    config = profile_root / "config/docking/dock.json"
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
    stack_trace = None
    if args.explorer_export and args.trace_surfaces:
        import faulthandler

        stack_trace = (output / "python-stacks.log").open("w", encoding="utf-8")
        faulthandler.dump_traceback_later(20, repeat=True, file=stack_trace)
    app_name = args.application_name or ("Character Map" if args.classic else "Notepad")
    expected_id = args.application_id or (
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
    report, shared, errors = (
        {
            "baseline_workarea": baseline,
            "drops": [],
            "runtime": {
                "implementation": "installed-wheel" if runtime.installed else "source",
                "wheel_sha256": runtime.wheel_sha256,
                "origins": runtime_origins(runtime.root, {"app": app.__file__}),
            },
        },
        {},
        [],
    )
    blocked, release = threading.Event(), threading.Event()
    if args.check_artwork:
        from docking.platform.desktop.implementations.windows import icons

        original_extract = icons.extract_icon

        def extract(target, size):
            if (
                target == "shell:AppsFolder\\" + expected_id
                and size == 16
                and not release.is_set()
            ):
                blocked.set()
                assert release.wait(90), "Real menu artwork was not released"
            return original_extract(target, size)

        icons.extract_icon = extract
    main_id = "Microsoft.WindowsAlarms_8wekyb3d8bbwe!App"
    widget_id = "Microsoft.WindowsAlarms_8wekyb3d8bbwe!ClockWidgets"
    if args.check_visibility:
        from docking.platform.native.windows import packages

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
        if args.check_artwork:
            original_previews = ui.window.preview._preview_service

            class UnavailableOwnedPreview:
                asynchronous = original_previews.asynchronous

                def capture(self, window_id, *, width, height):
                    if ui.window.preview.current_desktop_id == expected_id:
                        return None
                    return original_previews.capture(
                        window_id, width=width, height=height
                    )

            ui.window.preview._preview_service = UnavailableOwnedPreview()
            report["preview_capture_fixture"] = (
                "Unavailable for the probe's owned application"
            )
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

    def surface_snapshot(stage):
        if not args.trace_surfaces:
            return
        from docking.platform.native.windows.ffi import window_handle

        def gtk_windows():
            return [
                {
                    "hwnd": window_handle(w.get_window()) if w.get_realized() else None,
                    "title": w.get_title(),
                    "name": w.get_name(),
                    "type": w.get_type_hint().value_nick,
                    "visible": w.get_visible(),
                    "mapped": w.get_mapped(),
                    "classes": w.get_style_context().list_classes(),
                    "child_type": type(w.get_child()).__name__,
                }
                for w in Gtk.Window.list_toplevels()
            ]

        report.setdefault("surfaces", []).append(
            {
                "stage": stage,
                "gtk": call(gtk_windows),
                "native": native.windows(os.getpid()),
            }
        )

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
            if args.explorer_export:
                from tools.windows.visual.explorer import ExplorerFolder

                folder = ExplorerFolder(native, args.export_directory)
                folder.__enter__()
                shared["export_folder"] = folder
            if not args.check_visibility:
                registry = shared["kwargs"]["application_registry"]
                native.wait_for(
                    lambda: call(
                        lambda: (
                            a
                            if (a := registry.get(expected_id)) is not None
                            and a.export_file is not None
                            else None
                        )
                    ),
                    "Native launcher export was not prepared",
                    60,
                )

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
            if args.explorer_export:
                folder = shared["export_folder"]
                folder.window = next(
                    w for w in native.windows() if w["hwnd"] == folder.window["hwnd"]
                )
                left, top, right, _bottom = folder.window["rect"]
                native.click((left + right) // 2, top + 20)
            else:
                native.click(1100, 100)
            if args.classic and not args.explorer_export:
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
                    cache = shared["kwargs"]["icon_loader"]._application_icon_source
                    info = shared["kwargs"]["application_registry"].get(expected_id)
                    image = (
                        cache.get(info, 32)
                        if cache is not None and info is not None
                        else None
                    )
                    if image is None:
                        return False
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
            if args.check_artwork:
                assert blocked.wait(15), (
                    "The real 16px extraction did not reach the gate"
                )
                before = {
                    "row": id(row),
                    "query": call(entry.get_text),
                    "foreground": native.user.GetForegroundWindow(),
                }

                def menu_artwork():
                    cache = shared["kwargs"]["icon_loader"]._application_icon_source
                    native_image = cache.get(application, 16) if cache else None
                    image = row.get_child().get_children()[0]
                    pixbuf = image.get_pixbuf()
                    return (
                        native_image is not None
                        and pixbuf is not None
                        and bytes(pixbuf.get_pixels()) == native_image.pixels
                    )

                assert not call(menu_artwork)
                capture("menu-artwork-pending")
                release.set()
                native.wait_for(
                    lambda: call(menu_artwork), "Menu artwork did not update in place"
                )
                assert call(entry.get_text) == before["query"]
                assert native.user.GetForegroundWindow() == before["foreground"]
                assert call(lambda: row.get_parent()) is menu
                report["menu_artwork"] = {**before, "updated_in_place": True}
                capture("menu-artwork-ready")
            drag_ended = threading.Event()
            drag_started = threading.Event()

            def drag_begin(_row, context):
                drag_started.set()
                report["drag_source"] = {
                    "protocol": context.get_protocol().value_nick,
                    "targets": [target.name() for target in context.list_targets()],
                }
                if args.trace_surfaces:
                    from docking.platform.native.windows.ffi import window_handle

                    window = context.get_drag_window()
                    report["drag_source"]["window"] = (
                        window_handle(window) if window is not None else None
                    )

            call(lambda: row.connect("drag-begin", drag_begin))
            call(lambda: row.connect("drag-end", lambda *_args: drag_ended.set()))

            def drag_failed(_row, context, result):
                report["drag_failure"] = {
                    "result": result.value_nick,
                    "action": int(context.get_selected_action()),
                }
                return False

            call(lambda: row.connect("drag-failed", drag_failed))

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

            def begin_row_drag():
                # Keep sending motion until GTK observes the threshold crossing;
                # a stopped 12px gesture can leave coalesced Win32 input pending.
                offset = 0
                for step in range(1, 25):
                    offset = step * 2
                    native.user.SetCursorPos(int(sx + offset), int(sy))
                    if drag_started.wait(0.05):
                        break
                assert drag_started.wait(5), "Native row drag did not start"
                return offset

            if args.explorer_export:
                source = application.export_file
                assert source is not None and source.is_file()
                folder = shared["export_folder"]
                bounds = folder.window["rect"]
                destination = args.export_directory / source.name
                assert not destination.exists()

                tx, ty = bounds[2] - 80, bounds[1] + 250
                destination_window = native.window_at(tx, ty)
                assert destination_window == folder.window["hwnd"], (
                    "Export destination is covered",
                    destination_window,
                    folder.window,
                )
                report["export_drag_points"] = {
                    "source": [sx, sy],
                    "destination": [tx, ty],
                    "source_window": native.window_at(sx, sy),
                    "destination_window": destination_window,
                    "native_windows": native.windows(os.getpid()),
                }
                report["export"] = {
                    "path": str(destination),
                    "source": str(source),
                    "id": expected_id,
                    "executable": str(application.executable_path)
                    if application.executable_path is not None
                    else None,
                }
                drag_source = call(
                    lambda: shared["kwargs"]["surface_service"]._file_drags
                )
                assert drag_source is not None
                native.user.SetCursorPos(int(sx), int(sy))
                time.sleep(0.3)
                capture("export-source-ready")
                native.user.mouse_event(2, 0, 0, 0, 0)
                try:
                    # Start within the short menu row before moving vertically
                    # out of it; a large first jump can leave before GTK sees
                    # motion beyond its drag threshold.
                    offset = begin_row_drag()
                    native.wait_for(
                        lambda: drag_source._active,
                        "GTK did not hand off to the native Shell drag",
                    )
                    report["native_drag_active"] = True
                    for step in range(1, 31):
                        native.user.SetCursorPos(
                            int(sx + offset + (tx - sx - offset) * step / 30),
                            int(sy + (ty - sy) * step / 30),
                        )
                        time.sleep(0.05)
                    time.sleep(0.5)
                    capture("explorer-export-dragging")
                    if args.cancel_export:
                        native.key(0x1B)
                        native.wait_for(
                            lambda: not drag_source._active,
                            "Escape did not cancel the active native drag",
                        )
                        report["native_drag_cancelled"] = True
                        assert call(entry.get_text) == app_name
                    elif args.close_during_export:
                        # Close our Explorer while GTK still pumps, then close
                        # Docking with the mouse held and native OLE active.
                        folder.close()
                        assert drag_source._active
                        assert native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
                        native.wait_for(
                            lambda: (
                                not native.user.IsWindow(dock["hwnd"])
                                and not drag_source._active
                            ),
                            "Closing Docking did not cancel its active native drag",
                        )
                        report["native_close_during_drag"] = True
                finally:
                    native.user.mouse_event(4, 0, 0, 0, 0)
                native.wait_for(
                    lambda: not drag_source._active,
                    "Native Shell drag did not finish",
                )
                ended = drag_ended.wait(15)
                (output / "export-live.json").write_text(
                    json.dumps(report, indent=2), encoding="utf-8"
                )
                if not ended:
                    # Cancel only this owned native test drag before cleanup.
                    native.key(0x1B)
                    drag_ended.wait(5)
                assert ended, "External application drag did not end"
                if args.cancel_export or args.close_during_export:
                    assert not destination.exists(), "Cancelled drag copied a file"
                    assert source.is_file()
                else:
                    native.wait_for(
                        destination.is_file, "Explorer did not receive the shortcut"
                    )
                    assert source.is_file()
                    assert destination.read_bytes() == source.read_bytes()
                    report["export"]["bytes"] = destination.stat().st_size
                assert not report["drops"], (
                    "External export was routed back into the dock"
                )
                assert all(
                    p["kind"] == "applet"
                    for p in json.loads(config.read_text(encoding="utf-8"))["pinned"]
                )
                if args.close_during_export:
                    # GetWindowText can synchronously message a leftover GTK
                    # popup after Gtk.main exits. The independent parent checks
                    # visible windows and shell state after this process exits.
                    report["ok"] = True
                    return
                assert not any(matches_application(w) for w in native.windows())
                capture("explorer-export-finished")
                # EnumWindows/GetWindowText can synchronously message our own
                # GTK surfaces. Finish this observation while GTK still pumps.
                folder.close()
                if not args.close_during_export:
                    native.key(0x1B)
                    assert native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
                native.wait_for(
                    lambda: not native.user.IsWindow(dock["hwnd"]),
                    "Dock did not close after export",
                )
                report["ok"] = True
                return
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
                offset = begin_row_drag()
                file_drags = shared["kwargs"]["surface_service"]._file_drags
                assert file_drags is not None
                native.wait_for(
                    lambda: file_drags._active,
                    "Internal pin did not use the native file source",
                )
                report["native_file_source_active"] = True
                for step in range(1, 31):
                    native.user.SetCursorPos(
                        int(sx + offset + (tx - sx - offset) * step / 30),
                        int(sy + (ty - sy) * step / 30),
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
            received = report["drops"][-1]
            if received["info"] == 2:
                assert received["data"] == expected_id
            else:
                from docking.core.paths import local_path_from_uri_or_path

                assert received["info"] == 1
                uris = [line for line in received["data"].splitlines() if line]
                assert len(uris) == 1
                path = local_path_from_uri_or_path(uris[0])
                assert path is not None and path.samefile(application.export_file)
            assert report["drops"][-1]["protocol"] == "ole2"
            surface_snapshot("drop-finished")
            native.key(0x1B)
            time.sleep(0.7)
            surface_snapshot("menu-dismissed")
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
            if args.check_artwork:
                preview = ui.window.preview
                native.wait_for(
                    lambda: call(preview.get_visible),
                    "Hover did not open the owned preview",
                )

                def preview_artwork():
                    item = model.find_by_desktop_id(expected_id)
                    cache = shared["kwargs"]["icon_loader"]._application_icon_source
                    size = shared["kwargs"]["config"].scaled_icon_size
                    native_image = (
                        cache.get(item.application_info, size)
                        if cache
                        and item is not None
                        and item.application_info is not None
                        else None
                    )
                    if native_image is None or item is None or item.icon is None:
                        return False
                    if bytes(item.icon.get_pixels()) != native_image.pixels:
                        return False
                    expected = item.icon.scale_simple(64, 64, 2)
                    values = list(preview._thumbnail_images.values())
                    return (
                        len(values) == 1
                        and values[0][3] is None
                        and (
                            bytes(values[0][0].get_pixbuf().get_pixels())
                            == bytes(expected.get_pixels())
                        )
                    )

                native.wait_for(
                    lambda: call(preview_artwork),
                    "Unavailable-capture preview did not use current native artwork",
                )
                from docking.platform.native.windows.ffi import window_handle

                hwnd = call(lambda: window_handle(preview.get_window()))
                popup = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == hwnd
                )
                tooltips = call(
                    lambda: [
                        w
                        for w in Gtk.Window.list_toplevels()
                        if w.get_visible() and w.get_type_hint().value_nick == "tooltip"
                    ]
                )
                assert tooltips == [preview], "Ordinary tooltip overlaps the preview"
                import cairo

                def preview_surface():
                    width, height = preview.get_size()
                    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
                    preview.draw(cairo.Context(surface))
                    return surface

                surface = call(preview_surface)
                surface.flush()
                data = surface.get_data()
                border_alpha = [
                    data[3],
                    data[
                        (surface.get_height() // 2) * surface.get_stride() + 4 * 4 + 3
                    ],
                ]
                assert border_alpha == [0, 0], (
                    "Floating preview lost transparency",
                    border_alpha,
                )
                surface.write_to_png(str(output / "preview-alpha.png"))
                report["preview_artwork"] = {
                    "native_icon": True,
                    "tooltip_count": len(tooltips),
                    "border_alpha": border_alpha,
                    "window": popup,
                }
            surface_snapshot("application-launched")
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
            if "entry" in locals() and native.user.IsWindow(shared.get("dock_hwnd", 0)):
                report["actual_filter"] = call(entry.get_text)
            (output / "failure-detail.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            capture("failure")
        finally:
            release.set()
            if "export_folder" in shared:
                shared["export_folder"].close()
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
        if profile is not None:
            profile.cleanup()
        if stack_trace is not None:
            faulthandler.cancel_dump_traceback_later()
            stack_trace.close()


if __name__ == "__main__":
    main()
