"""Exercise Window Killer with real input and owned native fixture processes."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calculator", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires an owned interactive Windows desktop")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(output / "config")
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from docking.launcher import prepare_display

    prepare_display()
    import gi
    import psutil

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, GLib
    from PIL import ImageGrab

    import docking.app as app
    from docking.platform.backends.base import ActionResult
    from docking.platform.backends.windows.ffi import bind
    from docking.platform.backends.windows.picking import WindowsWindowPickService
    from tools.windows_desktop_driver import WindowsDesktop, read_shared_json

    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline, explorer = native.workarea(), native.explorer()
    ready = threading.Event()
    shared, errors, fixtures, allowed_pids = {}, [], [], set()
    report = {"pid": os.getpid(), "picks": [], "kills": [], "cases": {}}
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://windowkiller"},
                    {"kind": "applet", "target": "applet://clock"},
                    {"kind": "applet", "target": "applet://systemtray"},
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
        assert done.wait(30), "GTK did not service the observation"
        if failures:
            raise failures[0]
        return values[0]

    def publish():
        (output / "result.json").write_text(json.dumps(report, indent=2))

    @contextmanager
    def without_thread_privileges():
        """Exercise real access denial even when psutil enabled SeDebugPrivilege.

        Only this test call uses a duplicate impersonation token. Restore the
        original thread token and close every handle without changing process
        privileges, account configuration or machine policy.
        """
        pointer, unsigned, boolean = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        previous, primary, restricted = pointer(), pointer(), pointer()
        close = bind(kernel, "CloseHandle", boolean, pointer)
        set_thread = bind(
            advapi, "SetThreadToken", boolean, ctypes.POINTER(pointer), pointer
        )
        thread = bind(kernel, "GetCurrentThread", pointer)()
        process = bind(kernel, "GetCurrentProcess", pointer)()
        try:
            opened = bind(
                advapi,
                "OpenThreadToken",
                boolean,
                pointer,
                unsigned,
                boolean,
                ctypes.POINTER(pointer),
            )(thread, 0xC, True, ctypes.byref(previous))
            assert opened or ctypes.get_last_error() == 1008  # ERROR_NO_TOKEN.
            assert bind(
                advapi,
                "OpenProcessToken",
                boolean,
                pointer,
                unsigned,
                ctypes.POINTER(pointer),
            )(process, 0xA, ctypes.byref(primary))
            assert bind(
                advapi,
                "DuplicateTokenEx",
                boolean,
                pointer,
                unsigned,
                pointer,
                unsigned,
                unsigned,
                ctypes.POINTER(pointer),
            )(primary, 0x2C, None, 2, 2, ctypes.byref(restricted))
            assert bind(
                advapi,
                "AdjustTokenPrivileges",
                boolean,
                pointer,
                boolean,
                pointer,
                unsigned,
                pointer,
                pointer,
            )(restricted, True, None, 0, None, None)
            assert set_thread(None, restricted)
            yield
        finally:
            restored = set_thread(None, previous)
            for handle in (restricted, primary, previous):
                if handle.value:
                    close(handle)
            assert restored, "Original test thread token did not restore"

    def state(folder):
        try:
            return read_shared_json(folder / "state.json")
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def launch(name, *extra):
        folder = output / name
        folder.mkdir()
        child = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("windows_windowpicker_fixture.py")),
                "--output",
                str(folder),
                *extra,
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=(folder / "stderr.log").open("w"),
        )
        fixtures.append((child, folder))
        allowed_pids.add(child.pid)
        result = native.wait_for(
            lambda: state(folder), "Native fixture did not map", 30
        )
        assert result["pid"] == child.pid
        return child, folder, result

    def rectangle(hwnd):
        bounds = wintypes.RECT()
        assert native.user.GetWindowRect(hwnd, ctypes.byref(bounds))
        return bounds.left, bounds.top, bounds.right, bounds.bottom

    def center(hwnd):
        left, top, right, bottom = rectangle(hwnd)
        return (left + right) // 2, (top + bottom) // 2

    def focus(hwnd):
        left, top, *_rest = rectangle(hwnd)
        native.click(left + 100, top + 12)
        native.wait_for(
            lambda: native.user.GetForegroundWindow() == hwnd,
            "Fixture did not receive foreground",
        )

    def close_fixture(child, details):
        for hwnd in details["windows"].values():
            native.user.PostMessageW(hwnd, 0x10, 0, 0)
        assert child.wait(15) == 0

    def activate_calculator():
        from docking.platform.backends.windows.com import (
            GUID,
            HRESULT,
            apartment,
            method,
            release,
        )

        before = set(psutil.pids())
        with apartment() as ole:
            pointer, pid = ctypes.c_void_p(), ctypes.c_uint32()
            try:
                cls = GUID.parse("45ba127d-10a8-46ea-8ab7-56ea9078943c")
                iid = GUID.parse("2e941141-7f97-4756-ba1d-9decde894a3d")
                create = bind(
                    ole,
                    "CoCreateInstance",
                    HRESULT,
                    ctypes.POINTER(GUID),
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.POINTER(GUID),
                    ctypes.POINTER(ctypes.c_void_p),
                )
                assert (
                    create(
                        ctypes.byref(cls),
                        None,
                        4,
                        ctypes.byref(iid),
                        ctypes.byref(pointer),
                    )
                    >= 0
                )
                bind(
                    ole,
                    "CoAllowSetForegroundWindow",
                    HRESULT,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                )(pointer, None)
                assert (
                    method(
                        pointer,
                        3,
                        HRESULT,
                        ctypes.c_wchar_p,
                        ctypes.c_wchar_p,
                        ctypes.c_uint32,
                        ctypes.POINTER(ctypes.c_uint32),
                    )(
                        pointer,
                        "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
                        None,
                        0,
                        ctypes.byref(pid),
                    )
                    >= 0
                )
                assert pid.value and pid.value not in before, (
                    "Calculator already existed; do not terminate it"
                )
                allowed_pids.add(pid.value)
                return psutil.Process(pid.value)
            finally:
                release(pointer)

    def drive():
        dock = calculator = None
        close_sent = False
        try:
            assert ready.wait(90)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock did not map",
            )
            applet = call(lambda: shared["model"].get_applet("applet://windowkiller"))
            shared["applet"] = applet
            picker = shared["backend"].window_picker
            assert isinstance(picker, WindowsWindowPickService)
            assert shared["backend"].capabilities.supports_window_pick
            assert shared["backend"].capabilities.supports_process_kill
            assert call(lambda: applet._window_picker is picker)
            original_pick, original_kill = picker.pick_window_at, picker.kill
            restricted_case = threading.Event()
            expected_process = [None]

            def observed_pick(*, x, y):
                started = time.monotonic()
                target = original_pick(x=x, y=y)
                point = picker._api._geometry.point(x, y, Gdk.Display.get_default())
                hit = picker._api._at_point(wintypes.POINT(*point)) if point else 0
                root = picker._api._ancestor(hit, 2) if hit else 0
                name = ctypes.create_unicode_buffer(256)
                if root:
                    picker._api._class(root, name, len(name))
                report["picks"].append(
                    {
                        "point": [x, y],
                        "pid": target.pid if target else None,
                        "window": target.id.value if target else None,
                        "class": target.wm_class if target else None,
                        "seconds": time.monotonic() - started,
                        "native_hit": hit,
                        "native_root": root,
                        "native_class": name.value,
                        "native_pid": picker._api._pid(root) if root else None,
                    }
                )
                return target

            def observed_kill(window_id):
                target = picker._selected
                assert (
                    target is not None
                    and target.window.pid in allowed_pids
                    and target.window.pid == expected_process[0]
                ), (
                    "Refuse an unexpected process in the owned-fixture test",
                    target,
                )
                started = time.monotonic()
                native_error = None
                if restricted_case.is_set():
                    with without_thread_privileges():
                        result = original_kill(window_id)
                        native_error = ctypes.get_last_error()
                else:
                    result = original_kill(window_id)
                report["kills"].append(
                    {
                        "pid": target.window.pid,
                        "root_pid": target.root_pid,
                        "window": window_id.value,
                        "result": result.value,
                        "seconds": time.monotonic() - started,
                        "restricted_privileges": restricted_case.is_set(),
                        "native_error": native_error,
                    }
                )
                return result

            picker.pick_window_at, picker.kill = observed_pick, observed_kill

            def icon(desktop_id="applet://windowkiller"):
                window = shared["ui"].window
                frame = window.geometry.build_frame(main_cursor=-1.0)
                bounds = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == desktop_id
                )
                left, top, *_rest = rectangle(dock["hwnd"])
                scale = window.get_scale_factor()
                return left + (bounds.x + bounds.w / 2) * scale, top + (
                    bounds.y + bounds.h / 2
                ) * scale

            def open_picker():
                native.click(*call(icon))
                native.wait_for(
                    lambda: call(lambda: applet._overlay is not None),
                    "Actual applet click did not open capture",
                )

            def select(point, *, expected_pid=None):
                expected_process[0] = expected_pid
                before = len(report["picks"])
                open_picker()
                native.click(*point)
                native.wait_for(
                    lambda: len(report["picks"]) > before,
                    "Overlay click did not reach native picking",
                )
                native.wait_for(
                    lambda: call(lambda: applet._overlay is None),
                    "Capture did not dismiss after selection",
                )
                publish()

            def rows(pid):
                return call(
                    lambda: [
                        w.id.value
                        for w in shared["backend"].windows.list_all_windows()
                        if w.pid == pid
                    ]
                )

            first, _first_dir, first_state = launch("first")
            source = first_state["windows"]["Target"]
            focus(source)
            native.wait_for(
                lambda: len(rows(first.pid)) == 2, "Native running entries missing"
            )
            for _ in range(4):
                focus(source)
                open_picker()
                ImageGrab.grab(all_screens=True).save(output / "capture-overlay.png")
                native.key(0x1B)
                native.wait_for(
                    lambda: call(lambda: applet._overlay is None),
                    "Escape did not cancel",
                )
                native.wait_for(
                    lambda: native.user.GetForegroundWindow() == source,
                    "Cancellation did not restore the original foreground",
                )
            assert not report["picks"] and not report["kills"] and first.poll() is None
            report["cases"]["escape"] = {"cycles": 4, "foreground": source}
            select((1150, 100))
            assert report["picks"][-1]["pid"] is None and not report["kills"]
            select(call(lambda: icon("applet://clock")))
            assert report["picks"][-1]["pid"] is None and not report["kills"]
            assert native.explorer() == explorer
            report["cases"]["desktop_and_dock_ignored"] = True

            second, second_dir, second_state = launch("overlapping")
            focus(second_state["windows"]["Target"])
            point = center(second_state["button"])
            assert native.window_at(*point) == second_state["windows"]["Target"]
            select(point, expected_pid=second.pid)
            assert second.wait(15) == 1 and report["kills"][-1]["pid"] == second.pid
            assert first.poll() is None and state(second_dir)["child_clicks"] == 0
            native.wait_for(
                lambda: not rows(second.pid), "Terminated windows remained in the dock"
            )
            assert len(rows(first.pid)) == 2
            report["cases"]["topmost_child"] = {
                "killed": second.pid,
                "preserved": first.pid,
                "no_click_through": True,
                "running_entries_removed": True,
            }

            protected, protected_dir, protected_state = launch(
                "protected", "--protected"
            )
            focus(protected_state["windows"]["Target"])
            restricted_case.set()
            select(center(protected_state["button"]), expected_pid=protected.pid)
            restricted_case.clear()
            denied = report["kills"][-1]
            assert denied["pid"] == denied["root_pid"] == protected.pid
            assert denied["window"] == protected_state["windows"]["Target"]
            assert denied["result"] == "failed" and denied["native_error"] == 5
            assert (
                protected.poll() is None and state(protected_dir)["child_clicks"] == 0
            )
            native.click(*center(protected_state["button"]))
            native.wait_for(
                lambda: state(protected_dir)["child_clicks"] == 1,
                "Input grab survived a denied termination",
            )
            close_fixture(protected, protected_state)
            report["cases"]["access_denied"] = {
                "pid": protected.pid,
                "result": "failed",
                "normal_exit": 0,
            }

            host, _host_dir, host_state = launch("host", "--host")
            content, content_dir, content_state = launch(
                "content", "--parent", str(host_state["windows"]["Target"])
            )
            focus(host_state["windows"]["Target"])
            select(center(content_state["button"]), expected_pid=content.pid)
            assert content.wait(15) == 1 and host.poll() is None
            assert report["kills"][-1]["pid"] == content.pid
            assert report["kills"][-1]["root_pid"] == host.pid
            assert state(content_dir)["child_clicks"] == 0
            close_fixture(host, host_state)
            report["cases"]["hosted_content"] = {
                "content_pid": content.pid,
                "host_pid": host.pid,
                "content_exit": 1,
                "host_normal_exit": 0,
            }

            stale, stale_dir, stale_state = launch("stale")
            focus(stale_state["windows"]["Target"])
            x, y = center(stale_state["button"])
            scale = call(lambda: shared["ui"].window.get_scale_factor())
            target = call(lambda: original_pick(x=x // scale, y=y // scale))
            assert target is not None and target.pid == stale.pid
            created = call(lambda: picker._selected.created)
            independent_created = psutil.Process(stale.pid).create_time()
            assert (
                abs((created - 116444736000000000) / 10000000 - independent_created)
                < 0.001
            )
            native.user.PostMessageW(stale_state["windows"]["Target"], 0x10, 0, 0)
            native.wait_for(
                lambda: not native.user.IsWindow(stale_state["windows"]["Target"]),
                "Stale target did not close",
            )
            assert call(lambda: picker.pid_for(target.id)) is None
            assert call(lambda: original_kill(target.id)) is ActionResult.NOT_FOUND
            assert stale.poll() is None and native.user.IsWindow(
                stale_state["windows"]["Sibling"]
            )
            close_fixture(stale, state(stale_dir))
            report["cases"]["stale_window"] = {
                "pid": stale.pid,
                "creation_time": created,
                "independent_unix_created": independent_created,
                "result": "not_found",
            }

            hung, hung_dir, hung_state = launch("hung")
            focus(hung_state["windows"]["Target"])
            (hung_dir / "hang.request").write_text("owned fixture only")
            native.wait_for(lambda: state(hung_dir).get("hung"), "Fixture did not hang")
            time.sleep(6)
            report["hung_windows"] = native.windows(hung.pid)
            select(center(hung_state["button"]), expected_pid=hung.pid)
            assert report["picks"][-1]["pid"] == hung.pid, report["picks"][-1]
            assert hung.wait(15) == 1 and report["kills"][-1]["pid"] == hung.pid
            hit = report["picks"][-1]
            assert hit["native_class"] == "Ghost" and hit["native_pid"] != hung.pid
            assert psutil.Process(hit["native_pid"]).name().casefold() == "dwm.exe"
            report["cases"]["hung_process"] = {
                "pid": hung.pid,
                "exit_code": 1,
                "ghost_window": hit["native_root"],
                "dwm_pid_preserved": hit["native_pid"],
            }

            if args.calculator:
                calculator = activate_calculator()
                window = native.wait_for(
                    lambda: next(
                        (w for w in native.windows() if w["title"] == "Calculator"),
                        None,
                    ),
                    "Owned Calculator did not appear",
                    30,
                )
                owner = wintypes.DWORD()
                native.user.GetWindowThreadProcessId(
                    window["hwnd"], ctypes.byref(owner)
                )
                select(center(window["hwnd"]), expected_pid=calculator.pid)
                assert report["kills"][-1]["pid"] == calculator.pid
                calculator.wait(15)
                assert not calculator.is_running()
                report["cases"]["calculator"] = {
                    "activation_pid": calculator.pid,
                    "frame_pid": owner.value,
                    "class": window["class"],
                    "hosted": owner.value != calculator.pid,
                }
                calculator = None

            # Native handle counts surround repeated real queries on one live
            # fixture; no handle may survive an individual pick/query call.
            focus(source)
            kernel = ctypes.WinDLL("kernel32")
            current = bind(kernel, "GetCurrentProcess", ctypes.c_void_p)()
            count = bind(
                kernel,
                "GetProcessHandleCount",
                ctypes.c_int32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
            )

            def handles():
                value = ctypes.c_uint32()
                assert count(current, ctypes.byref(value))
                return value.value

            x, y = center(first_state["button"])

            def repeated_queries():
                for _ in range(10):
                    target = original_pick(x=x // scale, y=y // scale)
                    assert target is not None and picker.pid_for(target.id) == first.pid
                before = handles()
                for _ in range(100):
                    target = original_pick(x=x // scale, y=y // scale)
                    assert target is not None and picker.pid_for(target.id) == first.pid
                after = handles()
                assert after <= before + 1, (before, after)
                return {"before": before, "after": after, "queries": 100}

            report["cases"]["handle_ownership"] = call(repeated_queries)
            selected_id = call(lambda: picker._selected.window.id)
            call(picker.stop)
            assert call(lambda: original_pick(x=0, y=0)) is None
            assert call(lambda: original_kill(selected_id)) is ActionResult.NOT_FOUND
            call(picker.start)
            assert call(lambda: original_kill(selected_id)) is ActionResult.NOT_FOUND
            report["cases"]["service_restart"] = True
            focus(source)
            open_picker()
            report["closing_with_capture"] = True
            assert first.poll() is None
            native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            close_sent = True
            report.update(
                ok=True,
                no_x11_imports=not any(
                    name.startswith("docking.platform.backends.x11")
                    for name in sys.modules
                ),
            )
            assert report["no_x11_imports"]
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            if calculator is not None:
                for window in native.windows():
                    if window["title"] == "Calculator":
                        native.user.PostMessageW(window["hwnd"], 0x10, 0, 0)
            for child, folder in reversed(fixtures):
                if child.poll() is None:
                    details = state(folder)
                    if details:
                        for hwnd in details["windows"].values():
                            native.user.PostMessageW(hwnd, 0x10, 0, 0)
                    try:
                        child.wait(5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(10)
                        report.setdefault("forced_fixture_cleanup", []).append(
                            child.pid
                        )
            report["fixture_exits"] = {
                str(child.pid): child.returncode for child, _ in fixtures
            }
            if dock is not None and not close_sent:
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            publish()

    driver = threading.Thread(target=drive, name="windows-picker-smoke", daemon=False)
    driver.start()
    try:
        app.main()
        driver.join(30)
        assert not driver.is_alive()
        picker = shared["backend"].window_picker
        assert picker._stopped and picker._selected is None
        assert shared["applet"]._overlay is None
        assert native.workarea() == baseline and native.explorer() == explorer
        report.update(
            normal_shutdown=True, picker_stopped=True, workarea_after=baseline
        )
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
