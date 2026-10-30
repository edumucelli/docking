"""Pin or Explorer-drop a program/shortcut, restart Docking, and launch its pin."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path


def document_application(native, observe, route: str, report: dict):
    """Separate asynchronous initial discovery from a genuinely missing pin."""
    started, selected = time.monotonic(), None
    state = report["catalog_readiness"] = {}

    def complete():
        nonlocal selected
        pending, selected = observe()
        state.update(
            pending=pending,
            resolved=selected is not None,
            has_document_target=bool(selected and selected.document_target),
        )
        return not pending

    try:
        native.wait_for(complete, "Initial application catalog did not complete", 60)
        assert selected is not None, "Selected application pin did not resolve"
        if route == "installed":
            assert selected.document_target, (
                "Installed exact-ID document source was not prepared"
            )
        return selected
    finally:
        state["elapsed_ms"] = round((time.monotonic() - started) * 1000)


def document_release_target(
    *, observe, move, wait_for, identifier: str, record, clock=time.monotonic
):
    """Follow the live application target during one physical OLE gesture."""
    stable, since = None, None

    def ready():
        nonlocal stable, since
        current = record["observed"] = observe()
        observations = record.setdefault("observations", [])
        if not observations or observations[-1] != current:
            observations.append(current)
            del observations[:-16]
        assert current["live_item"], "Document application item was replaced"
        if current["cursor"] != current["point"]:
            assert move(*current["point"]), "Document pointer did not move"
            stable, since = None, None
            return False
        if (
            current["target"] != identifier
            or current["reported_target"] != identifier
            or current["rendered_target"] != identifier
            or current["native_window"] != current["dock_window"]
        ):
            stable, since = None, None
            return False
        now = clock()
        if current != stable:
            stable, since = current, now
            return False
        assert since is not None
        return current if now - since >= 0.4 else False

    settled = wait_for(ready, "Document application target did not settle", 5)
    final = record["before_release"] = observe()
    assert final == settled, "Document application target changed before release"


def guard_document_release(check, cancel, reject, record):
    """Keep target failure primary even when owned-source cancellation fails."""
    try:
        check()
    except BaseException:
        reject()
        try:
            cancel()
        except Exception as error:
            record["cancellation_error"] = {
                "class": type(error).__name__,
                "errno": error.errno if isinstance(error, OSError) else None,
            }
        raise


def cancel_document_drag(native, source, cleared, record):
    """Send Escape only to the still-owned foreground Explorer drag source."""
    current = next(
        (row for row in native.windows(source["pid"]) if row["hwnd"] == source["hwnd"]),
        None,
    )
    record["foreground"] = native.user.GetForegroundWindow()
    assert current is not None and all(
        current[key] == source[key] for key in ("pid", "hwnd", "class", "title")
    ), "Owned document drag source changed before cancellation"
    assert record["foreground"] == source["hwnd"], (
        "Owned document drag source is not foreground for cancellation"
    )
    native.key(0x1B)
    record["escape_sent"] = True
    native.wait_for(cleared, "Owned document drag did not cancel before release", 3)
    record["target_cleared"] = True


def document_icon_rectangle(frame, identifier: str, insert: int, gap, horizontal):
    """Use the live icon rectangle with the same gap shift as its rendering."""
    index, geometry = next(
        (index, geometry)
        for index, geometry in enumerate(frame.item_geometries)
        if geometry.item.desktop_id == identifier
    )
    rect = geometry.draw_rect
    shift = gap if insert >= 0 and index >= insert else 0
    return geometry.item, (
        rect.x + (shift if horizontal else 0),
        rect.y + (0 if horizontal else shift),
        rect.w,
        rect.h,
    )


def document_target_snapshot(native, ui, identifier, expected_item, dock_window):
    """Observe the current painted and native drop target in physical pixels."""
    import ctypes
    from ctypes import wintypes

    from docking.core.items import APP_KIND
    from docking.core.position import is_horizontal
    from docking.platform.backends.windows.ffi import bind

    window, dnd = ui.window, ui.input_controller.dnd
    insert = dnd.drop_insert_index
    frame = window.geometry.build_frame(main_cursor=-1.0, drop_insert_index=insert)
    rendered = window._current_or_build_geometry_frame(drop_insert_index=insert)

    horizontal = is_horizontal(window.config.pos)
    gap = window.config.icon_size + window.theme.item_padding
    item, rect = document_icon_rectangle(frame, identifier, insert, gap, horizontal)
    visible, painted = document_icon_rectangle(
        rendered, identifier, insert, gap, horizontal
    )
    left, top = (
        max(rect[0], painted[0]),
        max(rect[1], painted[1]),
    )
    right = min(rect[0] + rect[2], painted[0] + painted[2])
    bottom = min(rect[1] + rect[3], painted[1] + painted[3])
    assert right > left and bottom > top, "Document target is not visible"
    bounds, cursor = wintypes.RECT(), wintypes.POINT()
    assert native.user.GetWindowRect(dock_window, ctypes.byref(bounds))
    assert bind(
        native.user,
        "GetCursorPos",
        ctypes.c_int32,
        ctypes.POINTER(wintypes.POINT),
    )(ctypes.byref(cursor))
    scale = window.get_scale_factor()
    x, y = (
        (cursor.x - bounds.left) / scale,
        (cursor.y - bounds.top) / scale,
    )
    target = dnd._drop_target_item_at_point(frame, x=x, y=y, accepted_kinds=(APP_KIND,))
    return {
        # The second half avoids rounding to just before
        # the insertion boundary at an icon's centre.
        "point": [
            int(
                bounds.left
                + (left + (right - left) * (0.75 if horizontal else 0.5)) * scale
            ),
            int(
                bounds.top
                + (top + (bottom - top) * (0.5 if horizontal else 0.75)) * scale
            ),
        ],
        "cursor": [cursor.x, cursor.y],
        "rect": list(rect),
        "painted_rect": list(painted),
        "live_item": item
        is visible
        is expected_item
        is window.model.find_by_desktop_id(identifier),
        "target": target.desktop_id if target else None,
        "reported_target": dnd.drop_target_id,
        "rendered_target": identifier
        if painted[0] <= x < painted[0] + painted[2]
        and painted[1] <= y < painted[1] + painted[3]
        else None,
        "item_count": len(frame.item_geometries),
        "insert": insert,
        "slide_offset": round(
            window.renderer.slide_offsets.get(identifier, 0.0),
            2,
        ),
        "native_window": native.window_at(cursor.x, cursor.y),
        "dock_window": dock_window,
        "foreground": native.user.GetForegroundWindow(),
    }


def _phase(output: Path, phase: str, runtime) -> None:
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.core.items import APP_KIND
    from docking.core.paths import local_path_from_uri_or_path
    from docking.platform.applications.registration import executable_application_id
    from docking.platform.applications.types import ApplicationOrigin
    from tools.windows.visual.desktop_driver import WindowsDesktop, read_shared_json
    from tools.windows.visual.explorer import ExplorerFolder
    from tools.windows.visual.smoke import dock_window
    from tools.windows.visual.tray_acceptance import runtime_origins

    manifest = read_shared_json(output / "manifest.json")
    executable = Path(manifest["executable"])
    source = Path(manifest.get("source", manifest["executable"]))
    shortcut = "source" in manifest
    identifier = manifest.get("identifier") or executable_application_id(executable)
    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    shared, report, failures = (
        {},
        {
            "phase": phase,
            "id": identifier,
            "runtime": {
                "implementation": "installed-wheel" if runtime.installed else "source",
                "wheel_sha256": runtime.wheel_sha256,
                "origins": runtime_origins(runtime.root, {"app": app.__file__}),
            },
        },
        [],
    )
    ready = threading.Event()
    build = app.build_dock_window

    def observe_build(**kwargs):
        ui = build(**kwargs)
        shared.update(
            ui=ui,
            model=kwargs["model"],
            registry=kwargs["application_registry"],
            backend=kwargs["session_backend"],
        )
        menu = ui.input_controller._interactions._menu
        create_menu = menu._new_popup_menu

        def observe_menu():
            current = create_menu()
            shared["menu"] = current
            return current

        menu._new_popup_menu = observe_menu
        ready.set()
        return ui

    app.build_dock_window = observe_build
    if phase in ("drop", "documents"):
        from docking.ui.dnd import DnDHandler

        receive = DnDHandler._on_drag_data_received

        def observe_drop(handler, widget, context, x, y, selection, info, when):
            report["native_drop"] = {
                "protocol": context.get_protocol().value_nick,
                "uris": selection.get_uris(),
                "point": [x, y],
            }
            if phase == "documents":
                frame = handler._geometry_builder.build_frame(
                    main_cursor=-1.0, drop_insert_index=handler.drop_insert_index
                )
                item = handler._drop_target_item_at_point(
                    frame, x=x, y=y, accepted_kinds=(APP_KIND,)
                )
                report["native_drop"]["target"] = item.desktop_id if item else None
                if (
                    shared["reject_document_drop"]
                    or item is not shared["document_item"]
                ):
                    report["native_drop"]["rejected"] = True
                    shared["document_drop_rejected"].set()
                    Gtk.drag_finish(context, False, False, when)
                    return None
            return receive(handler, widget, context, x, y, selection, info, when)

        DnDHandler._on_drag_data_received = observe_drop

    def call(callback):
        done, values, errors = threading.Event(), [], []

        def invoke():
            try:
                values.append(callback())
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
            return False

        GLib.idle_add(invoke)
        assert done.wait(30), "GTK did not service the observation"
        if errors:
            raise errors[0]
        return values[0]

    def drive():
        dock, program_pid, explorer_folder = None, None, None
        try:
            assert ready.wait(90)
            dock = native.wait_for(
                lambda: dock_window(native.windows(os.getpid())),
                "Dock did not map",
            )

            def point():
                window = shared["ui"].window
                rect = next(
                    g.draw_rect
                    for g in window.geometry.build_frame().item_geometries
                    if g.item.desktop_id == identifier
                )
                bounds = next(
                    w["rect"]
                    for w in native.windows(os.getpid())
                    if w["hwnd"] == dock["hwnd"]
                )
                scale = window.get_scale_factor()
                return bounds[0] + (rect.x + rect.w / 2) * scale, bounds[1] + (
                    rect.y + rect.h / 2
                ) * scale

            def target():
                native.user.SetCursorPos(1100, 100)
                time.sleep(0.3)
                for _ in range(3):
                    position = call(point)
                    native.user.SetCursorPos(int(position[0]), int(position[1]))
                    time.sleep(0.3)
                assert native.window_at(*position) == dock["hwnd"]
                return position

            if phase == "pin":
                program = subprocess.Popen(
                    [str(executable), *manifest["argv"]], cwd=executable.parent
                )
                program_pid = program.pid
                report["program_pid"] = program_pid
                native.wait_for(
                    lambda: call(
                        lambda: shared["model"].find_by_desktop_id(identifier)
                    ),
                    "Unlisted executable did not reach the dock",
                )
                original_item = call(
                    lambda: shared["model"].find_by_desktop_id(identifier)
                )
                assert (
                    original_item.application_info.origin is ApplicationOrigin.RUNTIME
                )
                if manifest.get("uncataloged"):
                    candidates = call(
                        lambda: shared["registry"].resolve_all_by_wm_class(
                            executable.stem
                        )
                    )
                    assert not candidates, "Uncataloged fixture gained a catalog alias"
                    report["catalog_aliases"] = 0
                position = target()
                native.click(*position, right=True)

                def menu_point():
                    menu = shared.get("menu")
                    if menu is None or not menu.get_mapped():
                        return None
                    item = next(
                        (
                            item
                            for item in menu.get_children()
                            if isinstance(item, Gtk.MenuItem)
                            and item.get_label() == "Keep in Dock"
                        ),
                        None,
                    )
                    if item is None:
                        return None
                    allocation = item.get_allocation()
                    x, y = item.translate_coordinates(
                        menu, allocation.width // 2, allocation.height // 2
                    )
                    _valid, origin_x, origin_y = menu.get_window().get_origin()
                    return origin_x + x, origin_y + y

                position = native.wait_for(
                    lambda: call(menu_point), "Keep in Dock menu item missing"
                )
                ImageGrab.grab(all_screens=True).save(output / "pin-menu.png")
                native.click(*position)
                native.wait_for(
                    lambda: call(lambda: original_item.is_pinned),
                    "Native menu did not persist the pin",
                )
                assert (
                    call(lambda: shared["model"].find_by_desktop_id(identifier))
                    is original_item
                )
                assert original_item.is_running and not original_item.runtime_executable
                assert (
                    original_item.application_info.origin is ApplicationOrigin.GENERATED
                )
                assert Path(original_item.application_info.launch_target).is_file()
                report["same_running_item"] = True
            elif phase == "drop":
                with ExplorerFolder(native, source.parent) as folder:
                    rectangle = folder.file_rectangle(source)
                    report["explorer_source"] = rectangle

                    def insertion_point():
                        window = shared["ui"].window
                        geometry = next(
                            g
                            for g in window.geometry.build_frame().item_geometries
                            if g.item.desktop_id == "applet://clock"
                        )
                        rect = geometry.draw_rect
                        bounds = next(
                            w["rect"]
                            for w in native.windows(os.getpid())
                            if w["hwnd"] == dock["hwnd"]
                        )
                        scale = window.get_scale_factor()
                        return bounds[0] + (rect.x + 2) * scale, bounds[1] + (
                            rect.y + rect.h / 2
                        ) * scale

                    destination = call(insertion_point)
                    report["drop_point"] = destination
                    native.drag(
                        (rectangle["x"] + rectangle["width"] / 2, rectangle["y"] + 20),
                        destination,
                    )
                    item = native.wait_for(
                        lambda: call(
                            lambda: shared["model"].find_by_desktop_id(identifier)
                        ),
                        "Explorer drop did not create an executable pin",
                    )
                    assert (
                        item.kind == APP_KIND and item.is_pinned and not item.is_running
                    )
                    if not shortcut:
                        assert (
                            item.application_info.origin is ApplicationOrigin.GENERATED
                        )
                    else:
                        saved = Path(item.application_info.launch_target)
                        assert (
                            saved != source
                            and saved.read_bytes() == source.read_bytes()
                        )
                        report["binary_copy_preserved"] = True
                    assert report["native_drop"]["protocol"] == "ole2"
                    uris = report["native_drop"]["uris"]
                    assert len(uris) == 1
                    source_path = local_path_from_uri_or_path(uris[0])
                    assert source_path is not None and source_path.samefile(source)
                    assert source.is_file()
                    report["source_retained"] = True
                    ImageGrab.grab(all_screens=True).save(
                        output / "explorer-dropped.png"
                    )
                report.update(ok=True, actual_explorer_drop=True)
                return
            elif phase == "documents":
                import psutil

                files = [Path(value) for value in manifest["documents"]]
                facts_path = Path(manifest["facts"])
                selected = document_application(
                    native,
                    lambda: call(
                        lambda: (
                            shared["registry"].initial_discovery_pending,
                            shared["registry"].get(identifier),
                        )
                    ),
                    manifest["route"],
                    report,
                )
                report["document_drops"] = []
                for count in (1, 2):
                    facts_path.unlink(missing_ok=True)
                    report.pop("native_drop", None)
                    explorer_folder = ExplorerFolder(native, files[0].parent)
                    with explorer_folder as folder:
                        rectangle = folder.file_rectangle(files[0])
                        source_point = (
                            rectangle["x"] + rectangle["width"] / 2,
                            rectangle["y"] + 20,
                        )
                        if count == 2:
                            native.click(*source_point)
                            native.key(ord("A"), control=True)
                        shared["document_item"] = call(
                            lambda: shared["model"].find_by_desktop_id(identifier)
                        )
                        shared["reject_document_drop"] = False
                        shared["document_drop_rejected"] = threading.Event()
                        gesture: dict[str, object] = {"count": count}
                        report.setdefault("document_gestures", []).append(gesture)

                        def observe_target():
                            return document_target_snapshot(
                                native,
                                shared["ui"],
                                identifier,
                                shared["document_item"],
                                dock["hwnd"],
                            )

                        def before_release(
                            observe_target=observe_target,
                            gesture=gesture,
                            folder=folder,
                        ):
                            dnd = shared["ui"].input_controller.dnd
                            guard_document_release(
                                lambda: document_release_target(
                                    observe=lambda: call(observe_target),
                                    move=native.user.SetCursorPos,
                                    wait_for=native.wait_for,
                                    identifier=identifier,
                                    record=gesture,
                                ),
                                lambda: cancel_document_drag(
                                    native,
                                    folder.window,
                                    lambda: call(
                                        lambda: (
                                            dnd.drop_insert_index < 0
                                            and not dnd.drop_target_id
                                        )
                                    ),
                                    gesture.setdefault("cancellation", {}),
                                ),
                                lambda: shared.update(reject_document_drop=True),
                                gesture,
                            )

                        destination = call(observe_target)["point"]
                        native.drag(
                            source_point, destination, before_release=before_release
                        )
                        native.wait_for(
                            lambda: (
                                facts_path.is_file()
                                or shared["document_drop_rejected"].is_set()
                            ),
                            "Selected application did not report document arguments",
                            30,
                        )
                        assert not shared["document_drop_rejected"].is_set(), (
                            "Physical document release missed the requested application"
                        )
                        facts = read_shared_json(facts_path)
                        program_pid = facts["pid"]
                        assert Path(facts["executable"]).samefile(executable)
                        assert Path(facts["cwd"]).samefile(manifest["cwd"])
                        received = report["native_drop"]
                        assert (
                            received["protocol"] == "ole2"
                            and len(received["uris"]) == count
                        )
                        actual_files = [
                            local_path_from_uri_or_path(uri) for uri in received["uris"]
                        ]
                        assert all(path is not None for path in actual_files)
                        assert {path.resolve() for path in actual_files} == {
                            path.resolve() for path in files[:count]
                        }
                        assert facts["argv"] == manifest["argv"] + [
                            str(path).replace("/", "\\") for path in actual_files
                        ]
                        rows = native.wait_for(
                            lambda current_pid=program_pid: call(
                                lambda: [
                                    row
                                    for row in shared[
                                        "backend"
                                    ].windows.list_all_windows()
                                    if row.pid == current_pid
                                ]
                            ),
                            "Document-opened window did not group under its pin",
                        )
                        assert len(rows) == 1 and rows[0].desktop_id == identifier
                        report["document_drops"].append(
                            {
                                "count": count,
                                "protocol": received["protocol"],
                                "facts": facts,
                                "desktop_id": rows[0].desktop_id,
                                "selected_target": selected.launch_target,
                            }
                        )
                        ImageGrab.grab(all_screens=True).save(
                            output / f"documents-{count}.png"
                        )
                        process = psutil.Process(program_pid)
                        assert Path(process.exe()).samefile(executable)
                        for window in native.windows(program_pid):
                            assert native.user.PostMessageW(window["hwnd"], 0x10, 0, 0)
                        assert process.wait(20) == 0
                        program_pid = None
                report.update(ok=True, physical_document_input=True)
                return
            else:
                item = native.wait_for(
                    lambda: call(
                        lambda: shared["model"].find_by_desktop_id(identifier)
                    ),
                    "Persisted executable pin did not restore",
                )
                assert item.is_pinned and not item.is_running
                if not shortcut:
                    assert item.application_info.origin is ApplicationOrigin.GENERATED
                else:
                    assert not source.exists()
                    assert Path(item.application_info.launch_target).is_file()
                    report["original_shortcut_removed"] = True
                native.click(*target())
                facts_file = (
                    Path(manifest["facts"])
                    if shortcut
                    else executable.parent / "facts-generated.json"
                )
                native.wait_for(
                    facts_file.is_file, "Saved shortcut did not launch the executable"
                )
                facts = read_shared_json(facts_file)
                program_pid = facts["pid"]
                assert Path(facts["executable"]).samefile(executable)
                assert Path(facts["cwd"]).samefile(
                    Path(manifest["cwd"]) if shortcut else executable.parent
                )
                assert facts["argv"] == (manifest["argv"][2:] if shortcut else [])
                report["native_launch"] = facts

            def tracked():
                return call(
                    lambda: [
                        w
                        for w in shared["backend"].windows.list_all_windows()
                        if w.pid == program_pid
                    ]
                )

            count = 2 if manifest.get("multiple_windows") else 1
            rows = native.wait_for(
                lambda: current if len(current := tracked()) == count else None,
                "Executable windows were not tracked",
            )
            assert all(row.desktop_id == identifier for row in rows)
            assert (
                call(
                    lambda: len(
                        [
                            item
                            for item in shared["model"].visible_items()
                            if item.desktop_id == identifier
                        ]
                    )
                )
                == 1
            )
            if count == 2:
                item = call(lambda: shared["model"].find_by_desktop_id(identifier))
                assert item.instance_count == 2 and item.is_pinned
                handles = {row.id.value for row in rows}
                native_rows = native.windows(program_pid)
                assert handles == {row["hwnd"] for row in native_rows}
                ImageGrab.grab(all_screens=True).save(
                    output / f"{phase}-two-windows.png"
                )
                bounds = native_rows[0]["rect"]
                native.click(bounds[0] + 80, bounds[1] + 15)

                def active_handle():
                    foreground = native.user.GetForegroundWindow()
                    return next(
                        (
                            row.id.value
                            for row in tracked()
                            if row.active and row.id.value == foreground
                        ),
                        None,
                    )

                initial = native.wait_for(active_handle, "Fixture did not activate")
                foregrounds = [initial]
                for _ in range(4):
                    native.click(*target())
                    expected = next(h for h in handles if h != foregrounds[-1])
                    native.wait_for(
                        lambda expected=expected: active_handle() == expected,
                        "Pin did not cycle to the other native window",
                    )
                    foregrounds.append(expected)
                assert foregrounds[-1] == initial
                closing = native.user.GetForegroundWindow()
                bounds = next(
                    row["rect"]
                    for row in native.windows(program_pid)
                    if row["hwnd"] == closing
                )
                native.click(bounds[2] - 20, bounds[1] + 15)
                remaining = native.wait_for(
                    lambda: current if len(current := tracked()) == 1 else None,
                    "Closing one native window did not update its pin",
                )
                assert remaining[0].id.value != closing
                assert item.is_running and item.is_pinned and item.instance_count == 1
                native.click(*target())
                native.wait_for(
                    lambda: active_handle() == remaining[0].id.value,
                    "Surviving window did not activate from the pin",
                )
                report["multiple_windows"] = {
                    "handles": sorted(handles),
                    "cycle_foregrounds": foregrounds,
                    "count_after_close": item.instance_count,
                    "survivor": remaining[0].id.value,
                }
            ImageGrab.grab(all_screens=True).save(output / f"{phase}-grouped.png")
            report.update(ok=True, pid=program_pid, grouped=True)
        except Exception as exc:
            if explorer_folder is not None and explorer_folder.cleanup_failure:
                report["explorer_cleanup_failure"] = explorer_folder.cleanup_failure
            if program_pid is not None:
                report["native_windows"] = native.windows(program_pid)
                report["tracked_windows"] = call(
                    lambda: [
                        {
                            "hwnd": row.id.value,
                            "pid": row.pid,
                            "app_id": row.app_id,
                            "desktop_id": row.desktop_id,
                            "class": row.wm_class,
                            "skip_taskbar": row.skip_taskbar,
                        }
                        for row in shared["backend"].windows.list_all_windows()
                        if row.pid == program_pid
                    ]
                )
                report["catalog_candidates"] = call(
                    lambda: [
                        {
                            "id": application.desktop_id,
                            "executable": str(application.executable_path),
                        }
                        for application in shared["registry"].resolve_all_by_wm_class(
                            executable.stem
                        )
                    ]
                )
            failures.append(exc)
            report["error"] = repr(exc)
            ImageGrab.grab(all_screens=True).save(output / f"{phase}-failure.png")
        finally:
            if program_pid is not None:
                for window in native.windows(program_pid):
                    assert native.user.PostMessageW(window["hwnd"], 0x10, 0, 0)
                import psutil

                with suppress(psutil.NoSuchProcess):
                    assert psutil.Process(program_pid).wait(20) == 0
            (output / f"{phase}.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8"
            )
            if dock is not None:
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            else:
                GLib.idle_add(app._quit)

    driver = threading.Thread(target=drive, daemon=True)
    driver.start()
    app.main()
    driver.join(30)
    assert not driver.is_alive()
    report["normal_shutdown"] = True
    (output / f"{phase}.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    if failures:
        raise failures[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--installed-root", type=Path)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--phase", choices=("pin", "drop", "restart", "documents"))
    parser.add_argument("--explorer-drop", action="store_true")
    parser.add_argument("--multiple-windows", action="store_true")
    parser.add_argument(
        "--uncataloged",
        action="store_true",
        help="Create no matching Start menu entries",
    )
    parser.add_argument("--shortcut", choices=("installed", "unlisted"))
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires a native Windows desktop")
    from tools.windows.visual.tray_acceptance import select_runtime

    runtime = select_runtime(args.installed_root, args.wheel)
    if args.uncataloged and args.shortcut:
        parser.error("--uncataloged cannot use an installed/selected shortcut")
    if args.shortcut and not args.explorer_drop:
        parser.error("--shortcut requires --explorer-drop")
    output = args.output.resolve()
    if args.phase:
        _phase(output, args.phase, runtime)
        return
    if output.exists() and any(output.iterdir()):
        parser.error("Choose a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    from .classic_fixture import ClassicApplications, write_shortcut
    from .desktop_driver import WindowsDesktop, read_shared_json

    native = WindowsDesktop()
    baseline, explorer = native.workarea(), native.explorer()
    with ClassicApplications(output, install_shortcuts=not args.uncataloged) as fixture:
        if args.uncataloged:
            original = fixture.executables["Other"]
            unique = original.with_name(f"Unlisted-{fixture.root.name}.exe")
            original.rename(unique)
            fixture.executables["Other"] = unique
        assert not str(fixture.executables["Other"].parent).isascii()
        config_home = fixture.root / "config"
        config = config_home / "docking/dock.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps(
                {
                    "pinned": [{"kind": "applet", "target": "applet://clock"}],
                    "icon_size": 64,
                    "hide_mode": "none",
                    "left_click_action": "cycle" if args.multiple_windows else "toggle",
                    "startup_tips_enabled": False,
                    "update_check_enabled": False,
                }
            ),
            encoding="utf-8",
        )
        manifest = {
            "executable": str(fixture.executables["Other"]),
            "argv": fixture.arguments("Other"),
            "uncataloged": args.uncataloged,
        }
        if args.uncataloged:
            assert not fixture.links.exists()
        if args.shortcut:
            import shutil

            from docking.platform.backends.windows.appresolver import (
                shortcut_application_id,
            )
            from docking.platform.backends.windows.catalog import (
                read_shell_applications,
            )

            key = "First" if args.shortcut == "installed" else "Other"
            directory = fixture.root / "Explorer résumé 日本語"
            directory.mkdir()
            selected = directory / "Selected résumé 日本語.lnk"
            cwd = directory / "Working directory résumé 日本語"
            cwd.mkdir()
            if args.shortcut == "installed":
                original = fixture.links / (fixture.labels[key] + ".lnk")

                def catalog_id():
                    return next(
                        (
                            a.desktop_id
                            for a in read_shell_applications()
                            if a.name == fixture.labels[key]
                        ),
                        None,
                    )

                identifier = native.wait_for(
                    catalog_id, "Installed fixture not cataloged", 45
                )
                shutil.copyfile(original, selected)
                cwd = fixture.executables[key].parent / "working directory 0 résumé"
                selected_id = shortcut_application_id(selected)
                assert selected_id == identifier, (selected_id, identifier)
            else:
                write_shortcut(
                    selected,
                    executable=fixture.executables[key],
                    argv=fixture.arguments(key),
                    cwd=cwd,
                )
                identifier = shortcut_application_id(selected)
                assert identifier
            manifest = {
                "executable": str(fixture.executables[key]),
                "argv": fixture.arguments(key),
                "facts": str(fixture.facts_path(key)),
                "cwd": str(cwd),
                "source": str(selected),
                "identifier": identifier,
            }
        if args.multiple_windows:
            (Path(manifest["executable"]).parent / "fixture-two-windows.txt").touch()
            manifest["multiple_windows"] = True
        (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        env = {
            **os.environ,
            "XDG_CONFIG_HOME": str(config_home),
            "XDG_CACHE_HOME": str(fixture.root / "cache"),
            "PYTHONUTF8": "1",
        }
        for phase in ("drop" if args.explorer_drop else "pin", "restart"):
            if phase == "restart":
                if args.shortcut:
                    Path(manifest["source"]).unlink()
                # A pin created from UCRT64 must also restore from a normal
                # desktop launch, where Python uses backslash separators.
                env.pop("MSYSTEM", None)
            with (output / f"{phase}.log").open("wb") as stream:
                command = [
                    sys.executable,
                    "-m",
                    "tools.windows.visual.runtime_pin_smoke",
                    "--output",
                    str(output),
                    "--phase",
                    phase,
                ]
                if args.installed_root is not None:
                    command.extend(("--installed-root", str(args.installed_root)))
                if args.wheel is not None:
                    command.extend(("--wheel", str(args.wheel)))
                subprocess.run(
                    command,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=180,
                )
            assert read_shared_json(output / f"{phase}.json")["ok"]
            assert native.workarea() == baseline and native.explorer() == explorer
        persisted = read_shared_json(config)
        assert (
            len([entry for entry in persisted["pinned"] if entry["kind"] == "app"]) == 1
        )
        assert len(tuple((config.parent / "launchers").glob("*.lnk"))) == 1
        (output / "saved-config.json").write_text(
            json.dumps(persisted, indent=2), encoding="utf-8"
        )
    (output / "result.json").write_text(
        json.dumps(
            {
                "ok": True,
                "actual_menu_pin": not args.explorer_drop,
                "actual_explorer_drop": args.explorer_drop,
                "unicode_executable_directory": not str(
                    Path(manifest["executable"]).parent
                ).isascii(),
                "process_restart": True,
                "native_relaunch": True,
                "normal_shutdown": True,
                "fixture_cleanup": True,
                "shortcut": args.shortcut,
                "multiple_windows": args.multiple_windows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Native executable pin, restart, launch, grouping and cleanup passed")


if __name__ == "__main__":
    main()
