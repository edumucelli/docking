"""Pin or Explorer-drop an executable, restart Docking, and launch its pin."""

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


def _phase(output: Path, phase: str) -> None:
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

    manifest = read_shared_json(output / "manifest.json")
    executable = Path(manifest["executable"])
    identifier = executable_application_id(executable)
    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    shared, report, failures = {}, {"phase": phase, "id": identifier}, []
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
    if phase == "drop":
        from docking.ui.dnd import DnDHandler

        receive = DnDHandler._on_drag_data_received

        def observe_drop(handler, widget, context, x, y, selection, info, when):
            report["native_drop"] = {
                "protocol": context.get_protocol().value_nick,
                "uris": selection.get_uris(),
            }
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
        dock, program_pid = None, None
        try:
            assert ready.wait(90)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
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
                with ExplorerFolder(native, executable.parent) as folder:
                    source = folder.file_rectangle(executable)
                    report["explorer_source"] = source

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
                        (source["x"] + source["width"] / 2, source["y"] + 20),
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
                    assert item.application_info.origin is ApplicationOrigin.GENERATED
                    assert report["native_drop"]["protocol"] == "ole2"
                    uris = report["native_drop"]["uris"]
                    assert len(uris) == 1
                    source_path = local_path_from_uri_or_path(uris[0])
                    assert source_path is not None and source_path.samefile(executable)
                    assert executable.is_file()
                    report["source_retained"] = True
                    ImageGrab.grab(all_screens=True).save(
                        output / "explorer-dropped.png"
                    )
                report.update(ok=True, actual_explorer_drop=True)
                return
            else:
                item = native.wait_for(
                    lambda: call(
                        lambda: shared["model"].find_by_desktop_id(identifier)
                    ),
                    "Persisted executable pin did not restore",
                )
                assert item.is_pinned and not item.is_running
                assert item.application_info.origin is ApplicationOrigin.GENERATED
                native.click(*target())
                facts_file = executable.parent / "facts-generated.json"
                native.wait_for(
                    facts_file.is_file, "Saved shortcut did not launch the executable"
                )
                facts = read_shared_json(facts_file)
                program_pid = facts["pid"]
                assert Path(facts["executable"]).samefile(executable)
                assert Path(facts["cwd"]).samefile(executable.parent)
                assert facts["argv"] == []
                report["native_launch"] = facts

            def tracked():
                return call(
                    lambda: [
                        w
                        for w in shared["backend"].windows.list_all_windows()
                        if w.pid == program_pid
                    ]
                )

            rows = native.wait_for(tracked, "Executable window was not tracked")
            assert len(rows) == 1 and rows[0].desktop_id == identifier
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
            ImageGrab.grab(all_screens=True).save(output / f"{phase}-grouped.png")
            report.update(ok=True, pid=program_pid, grouped=True)
        except Exception as exc:
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
    if failures:
        raise failures[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("pin", "drop", "restart"))
    parser.add_argument("--explorer-drop", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires a native Windows desktop")
    output = args.output.resolve()
    if args.phase:
        _phase(output, args.phase)
        return
    if output.exists() and any(output.iterdir()):
        parser.error("Choose a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    from .classic_fixture import ClassicApplications
    from .desktop_driver import WindowsDesktop, read_shared_json

    native = WindowsDesktop()
    baseline, explorer = native.workarea(), native.explorer()
    with ClassicApplications(output) as fixture:
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
                    "startup_tips_enabled": False,
                    "update_check_enabled": False,
                }
            ),
            encoding="utf-8",
        )
        (output / "manifest.json").write_text(
            json.dumps(
                {
                    "executable": str(fixture.executables["Other"]),
                    "argv": fixture.arguments("Other"),
                }
            ),
            encoding="utf-8",
        )
        env = {
            **os.environ,
            "XDG_CONFIG_HOME": str(config_home),
            "XDG_CACHE_HOME": str(fixture.root / "cache"),
            "PYTHONUTF8": "1",
        }
        for phase in ("drop" if args.explorer_drop else "pin", "restart"):
            if phase == "restart":
                # A pin created from UCRT64 must also restore from a normal
                # desktop launch, where Python uses backslash separators.
                env.pop("MSYSTEM", None)
            with (output / f"{phase}.log").open("wb") as stream:
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "tools.windows.visual.runtime_pin_smoke",
                        "--output",
                        str(output),
                        "--phase",
                        phase,
                    ],
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
                "unicode_executable_directory": True,
                "process_restart": True,
                "native_relaunch": True,
                "normal_shutdown": True,
                "fixture_cleanup": True,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Native executable pin, restart, launch, grouping and cleanup passed")


if __name__ == "__main__":
    main()
