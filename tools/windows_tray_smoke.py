"""Verify the shared GTK tray against independent native notification clients.

Runs an isolated dock configuration and the bounded generated-icon fixture.
All activation/menu clicks use desktop input. The fixture records callbacks,
real window focus and native menu selection independently of Docking.
--restart-explorer probes shell recovery in an owned test VM.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from contextlib import suppress
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--task-manager", action="store_true")
    parser.add_argument(
        "--restart-explorer",
        action="store_true",
        help="Crash/restart Explorer twice in an owned test VM; no open folder windows",
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires an owned interactive Windows desktop")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True)
    cached = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json;from gi.repository import GLib;"
                "print(json.dumps(GLib.get_user_cache_dir()))"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    target = output / "cache/gstreamer-1.0"
    target.mkdir(parents=True)
    registries = list(
        (Path(json.loads(cached.stdout)) / "gstreamer-1.0").glob("registry.*.bin")
    )
    assert registries, "Prepare the complete GStreamer registry first"
    for registry in registries:
        shutil.copy2(registry, target / registry.name)
    os.environ["XDG_CONFIG_HOME"] = str(output / "config")
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.platform.backends.windows.ffi import bind
    from docking.platform.tray.windows import WindowsTrayBackend
    from tools.windows_desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline = native.workarea()
    user = ctypes.WinDLL("user32", use_last_error=True)
    explorer = bind(
        user, "FindWindowW", ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p
    )("Shell_TrayWnd", "")
    style = bind(
        user, "GetWindowLongPtrW", ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_int
    )
    before_style = style(explorer, -20)
    fixture_root = output / "fixture"
    fixture_log = (output / "fixture.log").open("w")
    fixture = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("windows_tray_fixture.py")),
            "--output",
            str(fixture_root),
            "--seconds",
            "300",
        ],
        stdout=fixture_log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    fixture_cache = {}

    def fixture_state():
        nonlocal fixture_cache
        with suppress(OSError, ValueError):
            fixture_cache = json.loads((fixture_root / "state.json").read_text())
        return fixture_cache

    try:
        native.wait_for(
            lambda: fixture_state().get("stage") == "ready",
            "Native tray fixture did not become ready",
            30,
        )
    except Exception:
        (fixture_root / "stop").touch()
        fixture.wait(20)
        fixture_log.close()
        raise
    fixture_initial = fixture_state()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    pinned = [
        {"kind": "applet", "target": "applet://systemtray"},
        {"kind": "applet", "target": "applet://clock"},
    ]
    if args.restart_explorer:
        pinned.append({"kind": "applet", "target": "applet://trash"})
    config.write_text(
        json.dumps(
            {
                "pinned": pinned,
                "icon_size": 64,
                "hide_mode": "none",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        )
    )
    report = {
        "fixture_pid": fixture.pid,
        "fixture_before_dock": fixture_initial,
        "workarea_before": baseline,
        "states": {},
    }
    ready = threading.Event()
    shared = {}
    errors = []
    original = app.build_dock_window

    def build(**kwargs):
        ui = original(**kwargs)
        shared.update(ui=ui, model=kwargs["model"])
        ready.set()
        return ui

    app.build_dock_window = build

    def publish():
        temporary = output / "result.tmp"
        temporary.write_text(json.dumps(report, indent=2))
        temporary.replace(output / "result.json")

    def call(callback):
        done = threading.Event()
        values = []
        failures = []

        def invoke():
            try:
                values.append(callback())
            except Exception as exc:
                failures.append(exc)
            finally:
                done.set()
            return False

        GLib.idle_add(invoke)
        assert done.wait(20), "GTK observation was not serviced"
        if failures:
            raise failures[0]
        return values[0]

    def capture(name):
        ImageGrab.grab(all_screens=True).save(output / (name + ".png"))

    def icon_rects():
        from ctypes import wintypes

        from docking.platform.backends.windows.com import GUID

        class Identifier(ctypes.Structure):
            _fields_ = [
                ("size", ctypes.c_uint32),
                ("window", ctypes.c_void_p),
                ("uid", ctypes.c_uint32),
                ("guid", GUID),
            ]

        query = bind(
            ctypes.WinDLL("shell32"),
            "Shell_NotifyIconGetRect",
            ctypes.c_int32,
            ctypes.POINTER(Identifier),
            ctypes.POINTER(wintypes.RECT),
        )
        result = {}
        for which, row in fixture_initial["icons"].items():
            identifier = Identifier(
                size=ctypes.sizeof(Identifier),
                window=fixture_initial["window"],
                uid=row["uid"],
                guid=GUID.parse(row["guid"] or "00000000-0000-0000-0000-000000000000"),
            )
            rect = wintypes.RECT()
            status = query(ctypes.byref(identifier), ctypes.byref(rect))
            result[which] = {
                "status": status,
                "rect": [rect.left, rect.top, rect.right, rect.bottom],
            }
        return result

    def recycle_round_trip(cycle):
        from docking.applets.trash.windows import WindowsRecycleBin

        trash = call(lambda: shared["model"].get_applet("applet://trash"))
        assert trash is not None
        recycle = WindowsRecycleBin()
        before = recycle.count()
        native.wait_for(
            lambda: call(lambda: trash._item_count) == before,
            "Recycle Bin baseline did not reach the applet",
        )
        root = Path(tempfile.mkdtemp(prefix="Docking-shell-recycle-")).resolve()
        file = root / "Owned shell restart document.txt"
        contents = "Docking shell restart fixture " + str(cycle)
        file.write_text(contents, encoding="utf-8")
        entry = {"cycle": cycle, "root": str(root), "before": before}
        report.setdefault("recycle_after_restart", []).append(entry)

        def restore():
            def literal(value):
                return "'" + str(value).replace("'", "''") + "'"

            program = (
                "$ErrorActionPreference='Stop';"
                "$s=New-Object -ComObject Shell.Application;"
                "$items=@($s.NameSpace(10).Items() | Where-Object {"
                "$_.ExtendedProperty('System.Recycle.DeletedFrom') -eq "
                + literal(root)
                + " -and $_.Name -eq "
                + literal(file.name)
                + "});if($items.Count -ne 1){throw 'Owned item missing or ambiguous'};"
                "if([IO.File]::ReadAllText($items[0].Path) -ne "
                + literal(contents)
                + "){throw 'Owned contents mismatch'};$items[0].InvokeVerb('undelete')"
            )
            subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    base64.b64encode(program.encode("utf-16-le")).decode("ascii"),
                ],
                capture_output=True,
                check=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            native.wait_for(file.exists, "Owned Recycle Bin item was not restored", 20)
            assert file.read_text(encoding="utf-8") == contents
            entry["restored"] = True

        try:
            assert recycle.recycle(str(file)) and not file.exists()
            entry["recycled"] = True
            native.wait_for(
                lambda: call(lambda: trash._item_count) == before + 1,
                "Recycle Bin observer did not receive the post-restart addition",
                20,
            )
            assert recycle.count() == before + 1
            entry["notification_add"] = True
            capture("restart-" + str(cycle) + "-trash-full")
            restore()
            native.wait_for(
                lambda: call(lambda: trash._item_count) == before,
                "Recycle Bin observer did not receive the post-restart restore",
                20,
            )
            assert recycle.count() == before
            entry["notification_restore"] = True
        finally:
            if entry.get("recycled") and not file.exists():
                restore()
            if file.exists():
                assert file.read_text(encoding="utf-8") == contents
                file.unlink()
                root.rmdir()
                entry["cleaned"] = True
            publish()

    def drive():
        dock = None
        task_process = task_window = None
        revision = 0
        try:
            assert ready.wait(90)
            applet = call(lambda: shared["model"].get_applet("applet://systemtray"))
            assert applet is not None and applet._presentation is None
            shared["applet"] = applet
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Tray dock did not map",
            )

            def state():
                return call(lambda: applet._state)

            def ours():
                return tuple(
                    i
                    for i in state().items
                    if i.title.startswith("Docking tray fixture ")
                )

            native.wait_for(
                lambda: state().available and len(ours()) == 2,
                "Both native tray clients did not reach the applet",
                30,
            )
            backend = applet._service._backend
            assert isinstance(backend, WindowsTrayBackend)
            shared["backend"] = backend
            assert "docking.applets.systemtray.xembed" not in sys.modules
            assert "gi.repository.GdkX11" not in sys.modules
            assert not any(
                n.startswith("docking.platform.status_notifier") for n in sys.modules
            )
            native.wait_for(
                lambda: all(r["adds"] >= 2 for r in fixture_state()["icons"].values()),
                "Existing icons did not re-register at host startup",
            )
            assert style(explorer, -20) == before_style
            report["seeded_existing_icons"] = True
            ids = {
                which: (
                    "guid:" + row["guid"]
                    if row["guid"]
                    else f"window:{fixture_initial['window']:08x}:{row['uid']}"
                )
                for which, row in fixture_initial["icons"].items()
            }

            def point():
                window = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                frame = shared["ui"].window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://systemtray"
                )
                return window["rect"][0] + rect.x + rect.w / 2, window["rect"][
                    1
                ] + rect.y + rect.h / 2

            def popup():
                visible = call(
                    lambda: applet._popup is not None and applet._popup.get_visible()
                )
                if not visible:
                    native.click(*call(point))
                native.wait_for(
                    lambda: call(
                        lambda: (
                            applet._popup is not None and applet._popup.get_visible()
                        )
                    ),
                    "System Tray popup did not map",
                )
                return call(lambda: applet._popup)

            def row_for(identifier):
                index = next(
                    index
                    for index, item in enumerate(applet._state.items)
                    if item.identifier == identifier
                )
                return applet._popup.get_child().get_child().get_children()[index + 1]

            def button_point(identifier, label):
                row = row_for(identifier)
                button = (
                    row.get_children()[0]
                    if label is None
                    else next(
                        w
                        for w in row.get_children()
                        if isinstance(w, Gtk.Button) and w.get_label() == label
                    )
                )
                window = applet._popup
                x, y = window.get_window().get_origin()[-2:]
                offset = button.translate_coordinates(
                    window,
                    button.get_allocated_width() // 2,
                    button.get_allocated_height() // 2,
                )
                assert offset is not None
                return x + offset[0], y + offset[1]

            def verify_pixels(which):
                item = next(i for i in state().items if i.identifier == ids[which])
                pixels = item.icon_pixmap
                assert pixels is not None and pixels.width == pixels.height == 32
                color = fixture_state()["icons"][which]["color"]
                offset = (16 * 32 + 5) * 4
                assert (
                    all(abs(pixels.rgba[offset + j] - color[j]) <= 2 for j in range(3))
                    and pixels.rgba[offset + 3] == 255
                )
                assert 126 <= pixels.rgba[(16 * 32 + 16) * 4 + 3] <= 130

                def observed():
                    widget = row_for(ids[which]).get_children()[0]
                    image = (
                        widget.get_child()
                        if isinstance(widget, Gtk.EventBox)
                        else widget
                    ).get_pixbuf()
                    assert (
                        image is not None
                        and image.get_width() == 24
                        and image.get_height() == 24
                    )
                    raw = image.get_pixels()
                    offset = 12 * image.get_rowstride() + 4 * 4
                    return list(raw[offset : offset + 4])

                row_color = call(observed)
                assert all(abs(row_color[j] - color[j]) <= 3 for j in range(3))
                return zlib.crc32(pixels.rgba)

            def snapshot(name):
                report["states"][name] = [
                    {
                        "id": i.identifier,
                        "title": i.title,
                        "bytes": len(i.icon_pixmap.rgba) if i.icon_pixmap else 0,
                    }
                    for i in ours()
                ]
                capture(name)
                publish()

            def command(action, which="modern"):
                nonlocal revision
                revision += 1
                temporary = fixture_root / "command.tmp"
                temporary.write_text(
                    json.dumps({"revision": revision, "action": action, "which": which})
                )
                for attempt in range(20):
                    try:
                        temporary.replace(fixture_root / "command.json")
                        break
                    except PermissionError:
                        if attempt == 19:
                            raise
                        time.sleep(0.02)
                native.wait_for(
                    lambda: fixture_state().get("command_revision") == revision,
                    "Tray fixture command did not complete",
                )

            shared["command"] = command

            if args.restart_explorer:
                reserved = native.wait_for(
                    lambda: area if (area := native.workarea()) != baseline else None,
                    "Initial dock space reservation did not settle",
                    15,
                )
                report["reserved_workarea"] = reserved
                report["shell_restarts"] = []
                for cycle in range(1, 3):
                    previous = fixture_state()["icons"]
                    entry = native.restart_explorer()
                    report["shell_restarts"].append(entry)
                    shared["explorer"] = entry["after"]["window"]
                    publish()
                    native.wait_for(
                        lambda: backend._api._explorer == shared["explorer"],
                        "Tray host did not find the replacement Explorer",
                        15,
                    )
                    native.wait_for(
                        lambda previous=previous: all(
                            row["adds"] > previous[which]["adds"]
                            for which, row in fixture_state()["icons"].items()
                        ),
                        "Native clients did not re-register after Explorer restart",
                        15,
                    )
                    entry["reregistered"] = fixture_state()["icons"]
                    native.wait_for(
                        lambda: native.workarea() == reserved,
                        "Dock space reservation did not recover after Explorer restart",
                        20,
                    )
                    entry["workarea"] = native.workarea()
                    assert style(shared["explorer"], -20) == before_style
                    assert len(ours()) == 2
                    popup()
                    snapshot("restart-" + str(cycle) + "-tray-popup")
                    native.click(1100, 100)
                    recycle_round_trip(cycle)
                    entry["icon_rects"] = icon_rects()
                    publish()

            popup()
            checks = {which: verify_pixels(which) for which in ids}
            report["native_icon_crc"] = checks
            snapshot("native-initial-popup")
            for which in ("legacy", "modern"):
                popup()
                before_double = fixture_state().get("double_clicks", 0)
                native.double_click(
                    *call(lambda which=which: button_point(ids[which], None))
                )
                native.wait_for(
                    lambda before_double=before_double: (
                        fixture_state().get("double_clicks", 0) > before_double
                    ),
                    "Native tray double-click callback did not arrive",
                )
                popup()
                before = fixture_state()["activations"]
                native.click(
                    *call(lambda which=which: button_point(ids[which], "Open"))
                )
                native.wait_for(
                    lambda which=which, before=before: (
                        fixture_state()["activations"] > before
                        and fixture_state().get("activated") == which
                    ),
                    "Real tray activation callback did not arrive",
                )
                native.wait_for(
                    lambda: (
                        native.user.GetForegroundWindow() == fixture_initial["window"]
                    ),
                    "Tray activation did not focus the native fixture window",
                )
                capture("native-" + which + "-activated")
                popup()
                before = fixture_state()["menu_actions"]
                native.click(
                    *call(lambda which=which: button_point(ids[which], "Menu"))
                )
                native.wait_for(
                    lambda: any(
                        w["class"] == "#32768" for w in native.windows(fixture.pid)
                    ),
                    "Native tray context menu did not map",
                )
                capture("native-" + which + "-context-menu")
                report[which + "_menu_foreground"] = native.user.GetForegroundWindow()
                native.key(0x28)
                report[which + "_menu_foreground_after_down"] = (
                    native.user.GetForegroundWindow()
                )
                native.key(0x0D)
                report[which + "_menu_foreground_after_enter"] = (
                    native.user.GetForegroundWindow()
                )
                capture("native-" + which + "-context-after-keys")
                publish()
                native.wait_for(
                    lambda before=before: fixture_state()["menu_actions"] > before,
                    "Native tray context menu selection was not delivered",
                )
            popup()
            command("modify")
            native.wait_for(
                lambda: any(
                    i.identifier == ids["modern"] and i.title.endswith("updated")
                    for i in ours()
                ),
                "Native icon update did not refresh the shared popup",
                15,
            )
            assert call(lambda: applet._popup.get_visible())
            assert verify_pixels("modern") != checks["modern"]
            snapshot("native-updated-popup")
            command("hide", "legacy")
            native.wait_for(
                lambda: all(i.identifier != ids["legacy"] for i in ours()),
                "Hidden native icon remained visible",
                15,
            )
            snapshot("native-hidden-legacy")
            command("show", "legacy")
            native.wait_for(
                lambda: any(i.identifier == ids["legacy"] for i in ours()),
                "Shown native icon did not return",
                15,
            )
            command("remove")
            native.wait_for(
                lambda: all(i.identifier != ids["modern"] for i in ours()),
                "Deleted native icon remained visible",
                15,
            )
            snapshot("native-modern-deleted")
            if args.task_manager:
                import psutil

                assert not any(
                    (p.info["name"] or "").casefold() == "taskmgr.exe"
                    for p in psutil.process_iter(["name"])
                )
                task_process = subprocess.Popen(
                    [str(Path(os.environ["WINDIR"]) / "System32/Taskmgr.exe")],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                task_window = native.wait_for(
                    lambda: next(
                        (w for w in native.windows() if w["title"] == "Task Manager"),
                        None,
                    ),
                    "Owned Task Manager window did not map",
                    30,
                )
                task_item = native.wait_for(
                    lambda: next(
                        (item for item in state().items if item.item_id == "taskmgr"),
                        None,
                    ),
                    "Real Task Manager notification icon did not reach Docking",
                    20,
                )
                bind(user, "ShowWindow", ctypes.c_int32, ctypes.c_void_p, ctypes.c_int)(
                    task_window["hwnd"], 6
                )
                iconic = bind(user, "IsIconic", ctypes.c_int32, ctypes.c_void_p)
                native.wait_for(
                    lambda: iconic(task_window["hwnd"]), "Task Manager did not minimize"
                )
                popup()
                snapshot("native-task-manager-popup")
                native.double_click(
                    *call(lambda: button_point(task_item.identifier, None))
                )
                native.wait_for(
                    lambda: (
                        not iconic(task_window["hwnd"])
                        and native.user.GetForegroundWindow() == task_window["hwnd"]
                    ),
                    "Task Manager did not restore and focus from its tray icon",
                    15,
                )
                capture("native-task-manager-restored")
                report["real_task_manager"] = {
                    "pid": task_window["pid"],
                    "window": task_window["hwnd"],
                    "item": task_item.identifier,
                    "restored": True,
                }
                native.user.PostMessageW(task_window["hwnd"], 0x10, 0, 0)
                native.wait_for(
                    lambda: not native.user.IsWindow(task_window["hwnd"]),
                    "Owned Task Manager did not close",
                    20,
                )
                task_window = None
                report["task_manager_exit"] = task_process.wait(20)
                assert report["task_manager_exit"] == 0
                task_process = None
                native.wait_for(
                    lambda: not any(i.item_id == "taskmgr" for i in state().items),
                    "Closed Task Manager left a stale tray item",
                    15,
                )
            if not args.restart_explorer:
                (fixture_root / "stop").touch()
                assert fixture.wait(20) == 0
                native.wait_for(
                    lambda: not ours(),
                    "Stopped native tray client remained visible",
                    15,
                )
                snapshot("native-fixture-stopped")
            else:
                command("add")  # Keep both clients alive through host shutdown.
            report.update(
                ok=True,
                legacy_and_modern_activation=True,
                native_menu_actions=fixture_state()["menu_actions"],
                native_double_clicks=fixture_state().get("double_clicks", 0),
                no_unix_imports=True,
            )
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            capture("failure")
        finally:
            if task_window is not None:
                native.user.PostMessageW(task_window["hwnd"], 0x10, 0, 0)
            if task_process is not None:
                try:
                    report["task_manager_exit"] = task_process.wait(20)
                except subprocess.TimeoutExpired:
                    report.update(ok=False, task_manager_cleanup_pending=True)
            if fixture.poll() is None and not args.restart_explorer:
                (fixture_root / "stop").touch()
                try:
                    fixture.wait(20)
                except subprocess.TimeoutExpired:
                    fixture.kill()
                    fixture.wait(10)
                    report.update(ok=False, forced_fixture_cleanup=True)
            report["fixture_exit"] = fixture.poll()
            if dock is not None:
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            elif ready.is_set():
                try:
                    call(lambda: shared["ui"].window.close())
                except Exception as exc:
                    errors.append(exc)
            publish()

    driver = threading.Thread(target=drive, name="windows-tray-smoke", daemon=False)
    driver.start()
    try:
        app.main()
        driver.join(90)
        assert not driver.is_alive()
        if args.restart_explorer:
            recovery = native.ensure_explorer()
            shared["explorer"] = recovery["window"]
            report["explorer_recovered"] = recovery
        report["workarea_after"] = native.workarea()
        current_explorer = shared.get("explorer", explorer)
        report["explorer_style_after"] = style(current_explorer, -20)
        assert (
            native.workarea() == baseline
            and style(current_explorer, -20) == before_style
        )
        backend = shared.get("backend")
        if backend is not None:
            thread = backend._api._thread
            report["host_thread_stopped"] = thread is not None and not thread.is_alive()
            assert report["host_thread_stopped"]
        report["normal_shutdown"] = True
        if errors:
            raise errors[0]
        if args.restart_explorer:

            def live_icon_rects():
                rows = icon_rects()
                return rows if all(r["status"] == 0 for r in rows.values()) else None

            report["explorer_icon_rects_after_close"] = native.wait_for(
                live_icon_rects,
                "Notification icons did not survive host close after shell restart",
                15,
            )
            shared["command"]("modify", "legacy")
            shared["command"]("modify", "modern")
            report["explorer_handoff_after_restart"] = True
    except Exception as exc:
        report.update(ok=False, error=repr(exc))
        raise
    finally:
        if fixture.poll() is None:
            (fixture_root / "stop").touch()
            fixture.wait(20)
        report["fixture_exit"] = fixture.poll()
        fixture_log.close()
        publish()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
