"""Exercise WorkspaceService and the existing applet on an owned Windows VM.

Only desktops created by this run are renamed or removed. Fixture processes
own every application window used for membership and cross-desktop activation.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


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
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.backends.base import ActionResult
    from docking.platform.backends.windows.com import HRESULT, method
    from docking.platform.backends.windows.ffi import bind
    from docking.platform.backends.windows.virtual_desktops import (
        WindowsVirtualDesktopAPI,
        _check,
        _object,
    )
    from tools.windows.desktop_driver import WindowsDesktop, read_shared_json

    native, api = WindowsDesktop(), WindowsVirtualDesktopAPI()
    assert api.supported()
    assert not any(w["title"] == "Docking" for w in native.windows())
    assert not any(w["title"] == "Calculator" for w in native.windows())
    registry = ApplicationRegistry()
    registry.refresh()
    application_id = next(
        info.desktop_id
        for info in registry.snapshot()
        if info.desktop_id.casefold()
        == "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App".casefold()
    )
    initial = api.read()
    original_id = next(d.id for d in initial.desktops if d.active)
    original_ids = {d.id for d in initial.desktops}
    baseline, explorer = native.workarea(), native.explorer()
    owned, children, errors, shared = [], [], [], {}
    ready = threading.Event()
    report = {
        "pid": os.getpid(),
        "build": sys.getwindowsversion().build,
        "stages": [],
        "fixture_assigned_app_id": application_id,
    }
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://workspaces"},
                    {"kind": "applet", "target": "applet://clock"},
                ],
                "icon_size": 64,
                "hide_mode": "none",
                "show_recent_apps": False,
                "current_workspace_only": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        )
    )
    original_build = app.build_dock_window

    def build(**kwargs):
        ui = original_build(**kwargs)
        shared.update(
            ui=ui,
            backend=kwargs["session_backend"],
            model=kwargs["model"],
            config=kwargs["config"],
        )
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
        assert done.wait(20), "GTK did not service the observation"
        if failures:
            raise failures[0]
        return values[0]

    def publish():
        (output / "result.json").write_text(json.dumps(report, indent=2))

    def stage(name):
        report["stages"].append(name)
        publish()

    def current():
        with api._session() as (stack, manager, _create):
            return api._id(_object(stack, manager, 6))

    def external_switch(identifier):
        with api._session() as (stack, manager, _create):
            desktop = next(
                d for d in api._desktops(stack, manager) if api._id(d) == identifier
            )
            _check(method(manager, 9, HRESULT, ctypes.c_void_p)(manager, desktop))
        native.wait_for(lambda: current() == identifier, "Native desktop switch failed")

    def create_desktop():
        with api._session() as (stack, manager, _create):
            desktop = _object(stack, manager, 11)
            identifier = api._id(desktop)
            assert identifier not in original_ids and identifier not in owned
            owned.append(identifier)
            report["created"] = list(owned)
            publish()
            return identifier

    def rename(identifier, name):
        assert identifier in owned
        with api._session() as (stack, manager, _create):
            desktop = next(
                d for d in api._desktops(stack, manager) if api._id(d) == identifier
            )
            library, value = ctypes.WinDLL("combase"), ctypes.c_void_p()
            make = bind(
                library,
                "WindowsCreateString",
                HRESULT,
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_void_p),
            )
            clear = bind(library, "WindowsDeleteString", HRESULT, ctypes.c_void_p)
            try:
                _check(make(name, len(name), ctypes.byref(value)))
                _check(
                    method(manager, 16, HRESULT, ctypes.c_void_p, ctypes.c_void_p)(
                        manager, desktop, value
                    )
                )
            finally:
                _check(clear(value))

    def remove(identifier):
        assert identifier in owned and identifier not in original_ids
        with api._session() as (stack, manager, _create):
            desktops = {api._id(d): d for d in api._desktops(stack, manager)}
            if identifier in desktops:
                _check(
                    method(manager, 13, HRESULT, ctypes.c_void_p, ctypes.c_void_p)(
                        manager, desktops[identifier], desktops[original_id]
                    )
                )

    def launch_fixture(label):
        folder = output / label
        folder.mkdir()
        with (folder / "stderr.log").open("w") as log:
            child = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("windowpicker_fixture.py")),
                    "--output",
                    str(folder),
                    "--app-ids",
                ],
                stdout=subprocess.DEVNULL,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        children.append(child)

        def state():
            try:
                return read_shared_json(folder / "state.json")
            except (FileNotFoundError, json.JSONDecodeError):
                return None

        data = native.wait_for(state, "Fixture did not map", 30)
        # Assign a registered ID to these explicitly labeled native fixtures
        # so the common registry/matcher path produces one four-window group.
        for name in data["windows"]:
            request = folder / ("identity-" + name + ".request.json")
            temporary = request.with_suffix(".tmp")
            temporary.write_text(json.dumps({"window": name, "app_id": application_id}))
            temporary.rename(request)
        native.wait_for(
            lambda: (
                (value := state()) is not None
                and set(value["app_ids"].values()) == {application_id}
            ),
            "Fixture did not apply its explicit application ID",
        )
        return child, data["windows"]["Target"]

    def drive():
        dock = service = None
        try:
            assert ready.wait(90)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock did not map",
            )
            service, windows = shared["backend"].workspaces, shared["backend"].windows
            applet = call(lambda: shared["model"].get_applet("applet://workspaces"))
            assert call(lambda: applet._workspace_service is service)
            native.wait_for(
                lambda: service.available, "Native workspace service unavailable"
            )
            assert shared["backend"].capabilities.supports_workspace_switch
            first_child, first = launch_fixture("first")
            second = create_desktop()
            rename(second, "Docking lab second")
            native.wait_for(
                lambda: call(
                    lambda: any(
                        d.id == second and d.name == "Docking lab second"
                        for d in service.list_workspaces()
                    )
                ),
                "Desktop creation/name not published",
            )

            def active(identifier):
                return current() == identifier and call(
                    lambda: service.active_workspace().id == identifier
                )

            def settle(identifier):
                native.wait_for(
                    lambda: active(identifier), "Desktop transition not observed"
                )
                native.wait_for(
                    lambda: (
                        call(lambda: applet._active_name)
                        == next(
                            d.name
                            for d in service.list_workspaces()
                            if d.id == identifier
                        )
                    ),
                    "Applet active label stale",
                )
                time.sleep(0.6)
                assert native.explorer() == explorer and native.workarea() != baseline

            def point():
                window = shared["ui"].window
                frame = window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://workspaces"
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

            def click(right=False):
                native.user.SetCursorPos(1100, 50)
                time.sleep(0.6)
                coordinate = call(point)
                assert native.window_at(*coordinate) == dock["hwnd"]
                native.click(*coordinate, right=right)

            click()
            settle(second)
            stage("native-applet-click")
            second_child, second_hwnd = launch_fixture("second")
            if len(original_ids) == 1:
                native.key(0x25, control=True, windows=True)
            else:
                external_switch(original_id)
            settle(original_id)
            stage("external-shell-switch")

            def rows():
                return call(
                    lambda: {
                        w.id.value: w
                        for w in windows.list_all_windows()
                        if w.pid in {first_child.pid, second_child.pid}
                    }
                )

            native.wait_for(
                lambda: (
                    len(rows()) == 4
                    and rows()[second_hwnd].on_current_workspace is False
                    and all(w.desktop_id == application_id for w in rows().values())
                ),
                "Other desktop windows missing",
            )
            values = rows()
            assert values[first].on_current_workspace is True
            assert (
                values[second_hwnd].workspace_id == second
                and not values[second_hwnd].visible
            )
            report["membership"] = {
                str(h): {
                    "desktop": w.workspace_id,
                    "current": w.on_current_workspace,
                    "visible": w.visible,
                }
                for h, w in values.items()
            }
            assert len(call(lambda: windows.list_windows(application_id))) == 4
            call(lambda: setattr(shared["config"], "current_workspace_only", True))
            call(windows.refresh)
            assert len(call(lambda: windows.list_windows(application_id))) == 2
            assert len(rows()) == 4
            stage("current-workspace-filter")
            call(lambda: setattr(shared["config"], "current_workspace_only", False))
            call(windows.refresh)
            assert (
                call(lambda: windows.activate(values[second_hwnd].id))
                is ActionResult.OK
            )
            settle(second)
            native.wait_for(
                lambda: native.user.GetForegroundWindow() == second_hwnd,
                "Offdesktop window did not receive native foreground",
            )
            assert service.last_result is ActionResult.OK
            stage("cross-desktop-window-activation")

            click(right=True)
            menu = native.wait_for(
                lambda: call(
                    lambda: (
                        value
                        if isinstance(value := Gtk.grab_get_current(), Gtk.Menu)
                        else None
                    )
                ),
                "Workspace menu did not open",
            )
            labels = call(
                lambda: [
                    row.get_label() for row in menu.get_children() if row.get_label()
                ]
            )
            report["menu_labels"] = labels
            assert any("Docking lab second" in label for label in labels)
            native.user.SetCursorPos(1100, 50)
            time.sleep(0.6)
            ImageGrab.grab(all_screens=True).save(output / "workspace-menu.png")
            native.key(0x24)
            native.key(0x0D)
            settle(original_id)
            stage("native-workspace-menu")
            native.user.SetCursorPos(*map(int, call(point)))
            time.sleep(0.6)
            native.user.SetCursorPos(*map(int, call(point)))
            assert native.window_at(*call(point)) == dock["hwnd"]
            native.scroll(-120)
            settle(second)
            stage("native-workspace-scroll")
            rename(second, "Docking lab renamed")
            native.wait_for(
                lambda: call(lambda: applet._active_name == "Docking lab renamed"),
                "Active desktop rename not reflected",
            )
            third = create_desktop()
            native.wait_for(
                lambda: len(service.list_workspaces()) == len(original_ids) + 2,
                "Third desktop not reflected",
            )
            remove(third)
            native.wait_for(
                lambda: len(service.list_workspaces()) == len(original_ids) + 1,
                "Removed desktop remained in model",
            )
            assert service.activate(third) is ActionResult.NOT_FOUND
            stage("external-name-list-removal")
            external_switch(original_id)
            settle(original_id)
            call(
                lambda: shared["backend"].surface.set_workspace_scope(
                    current_workspace_only=True
                )
            )
            # Allow the worker to unpin the owned view before switching. The
            # independent DWM-filtered HWND observation establishes its scope.
            time.sleep(1.0)
            external_switch(second)
            native.wait_for(
                lambda: (
                    not any(
                        w["hwnd"] == dock["hwnd"] for w in native.windows(os.getpid())
                    )
                ),
                "Current-desktop dock remained pinned",
            )
            external_switch(original_id)
            native.wait_for(
                lambda: any(
                    w["hwnd"] == dock["hwnd"] for w in native.windows(os.getpid())
                ),
                "Unpinned dock missing on its original desktop",
            )
            call(
                lambda: shared["backend"].surface.set_workspace_scope(
                    current_workspace_only=False
                )
            )
            time.sleep(1.0)
            external_switch(second)
            native.wait_for(
                lambda: any(
                    w["hwnd"] == dock["hwnd"] for w in native.windows(os.getpid())
                ),
                "Repinned dock missing after switch",
            )
            settle(second)
            stage("surface-unpin-repin")
            ImageGrab.grab(all_screens=True).save(output / "workspaces.png")
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            try:
                for child in children:
                    if child.poll() is None:
                        for row in native.windows(child.pid, include_cloaked=True):
                            native.user.PostMessageW(row["hwnd"], 0x10, 0, 0)
                        assert child.wait(15) == 0
                external_switch(original_id)
                for identifier in reversed(owned):
                    remove(identifier)
                final = api.read()
                assert {d.id for d in final.desktops} == original_ids
                assert next(d.id for d in final.desktops if d.active) == original_id
                report["desktops_restored"] = True
            except Exception as exc:
                errors.append(exc)
                report.update(ok=False, cleanup_error=repr(exc))
            if dock is not None:
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            publish()

    driver = threading.Thread(
        target=drive, name="windows-workspace-smoke", daemon=False
    )
    driver.start()
    try:
        app.main()
        driver.join(40)
        assert not driver.is_alive()
        assert native.workarea() == baseline and native.explorer() == explorer
        assert all(child.poll() == 0 for child in children)
        assert service_stopped(shared["backend"].workspaces)
        report.update(normal_shutdown=True, workarea_after=baseline)
        if errors:
            raise errors[0]
    finally:
        app.build_dock_window = original_build
        publish()
        print(json.dumps(report, indent=2))


def service_stopped(service):
    return (
        not service.available
        and not service.pending
        and not service._sources
        and service._thread is None
    )


if __name__ == "__main__":
    main()
