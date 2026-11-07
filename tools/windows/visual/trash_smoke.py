"""Drive Recycle Bin open, real Explorer drops, notifications and confirmation.

Requires an empty Recycle Bin and no existing dock. Creates its own local
fixtures and verifies the complete shell inventory before confirming Empty.
Failed runs retain recoverable fixtures for inspection instead of emptying.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exercise-internal", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python")
    output = args.output.resolve()
    if (output / "result.json").exists():
        parser.error("Choose a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(output / "config")
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.applets.trash.implementations.windows.backend import WindowsRecycleBin
    from docking.platform.native.windows.hotkeys import (
        MOD_ALT,
        MOD_CONTROL,
        WindowsHotkeyAPI,
    )
    from docking.ui.dnd import DnDHandler
    from tools.windows.visual.desktop_driver import WindowsDesktop

    native, recycle = WindowsDesktop(), WindowsRecycleBin()
    assert not any(w["title"] == "Docking" for w in native.windows())
    assert recycle.count() == 0, "Preserve existing Recycle Bin items; use a clean lab"
    baseline = native.workarea()
    initial_windows = {w["hwnd"] for w in native.windows()}
    root = Path(tempfile.mkdtemp(prefix="Docking-recycle-ui-")).resolve()
    file = root / "Résumé # 100%.txt"
    file.write_text("owned recyclable document", encoding="utf-8")
    folder = root / "Folder café + 100%"
    folder.mkdir()
    (folder / "keep.txt").write_text("owned recyclable folder", encoding="utf-8")
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    pinned = [
        {"kind": "applet", "target": "applet://trash"},
        {"kind": "applet", "target": "applet://clock"},
    ]
    if args.exercise_internal:
        pinned.append({"kind": "applet", "target": "applet://calendar"})
    config.write_text(
        json.dumps(
            {
                "pinned": pinned,
                "icon_size": 48,
                "hide_mode": "none",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    report = {"baseline_workarea": baseline, "fixture": str(root)}
    original_motion = DnDHandler._on_drag_motion
    original_target = DnDHandler._drop_target_item_at_point
    original_received = DnDHandler._on_drag_data_received
    original_begin = DnDHandler._on_drag_begin
    original_end = DnDHandler._on_drag_end
    original_leave = DnDHandler._on_drag_leave

    def drag_state(handler):
        position = wintypes.POINT()
        native.user.GetCursorPos(ctypes.byref(position))
        return {
            "from": handler._drag_from,
            "index": handler.drag_index,
            "item": handler._dragged_item.desktop_id if handler._dragged_item else None,
            "left": handler._internal_drag_left_dock,
            "committed": handler._drop_committed,
            "native_pointer": [position.x, position.y],
        }

    def observe_begin(handler, widget, context):
        shared["dnd"] = handler
        original_begin(handler, widget, context)
        report.setdefault("drag_begin", []).append(drag_state(handler))

    def observe_end(handler, widget, context):
        from docking.ui.display import get_pointer_position

        before = drag_state(handler)
        original_end(handler, widget, context)
        after = drag_state(handler)
        after["gdk_pointer"] = get_pointer_position(handler._window.get_display())
        report.setdefault("drag_end", []).append({"before": before, "after": after})

    def observe_leave(handler, widget, context, time):
        original_leave(handler, widget, context, time)
        report.setdefault("drag_leave", []).append(drag_state(handler))

    def observe_received(handler, widget, context, x, y, selection, info, time):
        report.setdefault("drop_protocols", []).append(
            context.get_protocol().value_nick
        )
        return original_received(handler, widget, context, x, y, selection, info, time)

    def observe_target(handler, frame, *, x, y, accepted_kinds):
        target = original_target(
            handler, frame, x=x, y=y, accepted_kinds=accepted_kinds
        )
        report.setdefault("target_observations", []).append(
            {
                "x": x,
                "y": y,
                "insertion": handler.drop_insert_index,
                "target": target.desktop_id if target else None,
                "cursor_rect": [
                    frame.cursor_rect.x,
                    frame.cursor_rect.y,
                    frame.cursor_rect.w,
                    frame.cursor_rect.h,
                ],
                "items": [
                    {
                        "id": g.item.desktop_id,
                        "rect": [
                            g.draw_rect.x,
                            g.draw_rect.y,
                            g.draw_rect.w,
                            g.draw_rect.h,
                        ],
                    }
                    for g in frame.item_geometries
                ],
            }
        )
        return target

    def observe_motion(handler, widget, context, x, y, time):
        result = original_motion(handler, widget, context, x, y, time)
        report.setdefault("drag_motion", []).append(
            {
                "x": x,
                "y": y,
                "insertion": handler.drop_insert_index,
                "target": handler.drop_target_id,
            }
        )
        return result

    DnDHandler._drop_target_item_at_point = observe_target
    DnDHandler._on_drag_motion = observe_motion
    DnDHandler._on_drag_data_received = observe_received
    DnDHandler._on_drag_begin = observe_begin
    DnDHandler._on_drag_end = observe_end
    DnDHandler._on_drag_leave = observe_leave
    shared, errors, owned = {}, [], []
    ready = threading.Event()
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

    def powershell(program):
        program = (
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)\n"
            + program
        )
        encoded = base64.b64encode(program.encode("utf-16-le")).decode("ascii")
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8-sig",
            timeout=30,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return result.stdout

    def inventory():
        rows = json.loads(
            powershell("""$s=New-Object -ComObject Shell.Application
$rows=@($s.NameSpace(10).Items() | ForEach-Object {
 [pscustomobject]@{name=$_.Name;path=$_.Path;location=$_.ExtendedProperty('System.Recycle.DeletedFrom')}
});ConvertTo-Json -InputObject $rows -Compress""")
        )
        for row in rows:
            assert Path(row["location"]).samefile(root), (
                "Unowned item appeared; preserve it"
            )
            assert row["name"] in {file.name, folder.name}, row
        return rows

    def drive():
        try:
            assert ready.wait(90)
            ui, model = shared["ui"], shared["model"]
            trash = call(lambda: model.get_applet("applet://trash"))
            assert trash is not None and trash._backend.name == "windows"
            original_open = trash._backend.open

            def observe_open():
                report["open_calls"] = report.get("open_calls", 0) + 1
                original_open()

            trash._backend.open = observe_open
            assert call(lambda: trash._native_monitor_stop is not None), (
                "Native monitor did not start"
            )
            observer = call(lambda: trash._native_monitor_stop.__self__)
            shared["observer"] = observer
            report["native_monitor"] = {
                "hwnd": observer._hwnd,
                "registration": observer._registration,
            }
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock did not map",
            )

            def point(*, fresh=True, target_id="applet://trash"):
                frame = (
                    ui.window.geometry.build_frame(main_cursor=-1.0)
                    if fresh
                    else ui.window.current_interaction_frame()
                )
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == target_id
                )
                current = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                return current["rect"][0] + rect.x + rect.w / 2, current["rect"][
                    1
                ] + rect.y + rect.h / 2

            def count(expected):
                native.wait_for(
                    lambda: call(lambda: trash._item_count) == expected,
                    f"Native shell notification did not update count to {expected}",
                )
                assert recycle.count() == expected
                report.setdefault("counts", []).append(expected)

            time.sleep(2)
            count(0)
            # A separate COM operation changes the shell, not the applet model.
            assert recycle.recycle(str(file))
            count(1)
            rows = inventory()
            assert (
                len(rows) == 1
                and Path(rows[0]["path"]).read_text(encoding="utf-8")
                == "owned recyclable document"
            )
            report["external_recycle"] = rows
            native.user.SetCursorPos(*map(int, call(point)))
            time.sleep(0.6)
            report["open_point"] = call(point)
            native.click(*report["open_point"])
            bin_window = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows()
                        if w["title"].startswith("Recycle Bin")
                        and w["class"] == "CabinetWClass"
                    ),
                    None,
                ),
                "Click did not open Recycle Bin",
            )
            if bin_window["hwnd"] not in initial_windows:
                owned.append(bin_window)
            report["recycle_bin_window"] = bin_window
            time.sleep(0.7)  # Capture the settled desktop after DWM's opening fade.
            capture("recycle-bin-open")
            # Restore the verified owned document using the shell's own verb.
            expected = str(root).replace("'", "''")
            powershell(
                "$s=New-Object -ComObject Shell.Application;"
                "$items=@($s.NameSpace(10).Items());"
                "if($items.Count -ne 1){throw 'Unexpected items'};$i=$items[0];"
                "if($i.ExtendedProperty('System.Recycle.DeletedFrom') -ne '"
                + expected
                + "'){throw 'Not owned'};$i.InvokeVerb('undelete')"
            )
            native.wait_for(file.exists, "Shell restore did not restore the document")
            assert file.read_text(encoding="utf-8") == "owned recyclable document"
            count(0)
            report["restored_document"] = True
            native.user.PostMessageW(bin_window["hwnd"], 0x10, 0, 0)
            os.startfile(str(root), "open")
            explorer = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows()
                        if w["title"].startswith(root.name)
                        and w["class"] == "CabinetWClass"
                    ),
                    None,
                ),
                "Fixture folder did not open",
            )
            owned.append(explorer)
            x1, y1, x2, _y2 = explorer["rect"]
            native.click((x1 + x2) // 2, y1 + 20)
            native.key(ord("2"), control=True, shift=True)
            time.sleep(1)
            name = folder.name.replace("'", "''")
            source = json.loads(
                powershell(f"""Add-Type -AssemblyName UIAutomationClient
$w=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{explorer["hwnd"]})
$scope=[System.Windows.Automation.TreeScope]::Descendants
$condition=[System.Windows.Automation.Condition]::TrueCondition
foreach($i in $w.FindAll($scope,$condition)){{
 if($i.Current.Name -eq '{name}'){{
  $r=$i.Current.BoundingRectangle
  if($r.Width -gt 20 -and $r.Height -gt 20){{
   @{{x=$r.Left;y=$r.Top;width=$r.Width;height=$r.Height;
      name=$i.Current.Name}}|ConvertTo-Json -Compress;break
  }}
 }}
}}""")
            )
            report["drag_source"] = source
            sx, sy = source["x"] + source["width"] / 2, source["y"] + 20
            target = call(lambda: point(fresh=True))
            report["drag_target"] = target
            native.user.SetCursorPos(int(sx), int(sy))
            native.user.mouse_event(2, 0, 0, 0, 0)
            try:
                for step in range(1, 31):
                    native.user.SetCursorPos(
                        int(sx + (target[0] - sx) * step / 30),
                        int(sy + (target[1] - sy) * step / 30),
                    )
                    time.sleep(0.05)
                # Window inventory updates can reflow running icons during a
                # drag. Follow the current target before releasing the mouse.
                for _ in range(3):
                    target = call(lambda: point(fresh=True))
                    native.user.SetCursorPos(int(target[0]), int(target[1]))
                    report.setdefault("drag_target_updates", []).append(target)
                    time.sleep(0.3)
                time.sleep(1)
                capture("explorer-dragging")
            finally:
                native.user.mouse_event(4, 0, 0, 0, 0)
            native.wait_for(
                lambda: not folder.exists(),
                "Explorer drop did not recycle the folder",
                30,
            )
            count(1)
            rows = inventory()
            assert len(rows) == 1 and rows[0]["name"] == folder.name
            assert (Path(rows[0]["path"]) / "keep.txt").read_text(
                encoding="utf-8"
            ) == "owned recyclable folder"
            report["native_explorer_drop"] = rows
            native.user.PostMessageW(explorer["hwnd"], 0x10, 0, 0)
            capture("trash-full")

            def empty_dialog():
                # Check complete shell ownership immediately before an Empty action.
                assert len(inventory()) == 1 and recycle.count() == 1
                native.click(*call(point), right=True)
                native.wait_for(
                    lambda: call(
                        lambda: isinstance(Gtk.grab_get_current(), Gtk.MenuShell)
                    ),
                    "Trash menu did not open",
                )
                capture("trash-menu")
                native.key(0x24)
                native.key(0x28)
                native.key(0x0D)
                dialog = native.wait_for(
                    lambda: call(
                        lambda: next(
                            (
                                w
                                for w in Gtk.Window.list_toplevels()
                                if isinstance(w, Gtk.MessageDialog) and w.get_visible()
                            ),
                            None,
                        )
                    ),
                    "Empty confirmation did not open",
                )
                window = native.wait_for(
                    lambda: next(
                        (
                            w
                            for w in native.windows(os.getpid())
                            if w["hwnd"] != dock["hwnd"]
                            and w["class"] == "gdkWindowToplevel"
                            and w["rect"][3] - w["rect"][1] > 100
                        ),
                        None,
                    ),
                    "Confirmation has no native window",
                )
                assert native.user.GetForegroundWindow() == window["hwnd"], (
                    "Confirmation did not gain native focus"
                )
                time.sleep(0.5)
                capture("empty-confirmation")
                return dialog, window

            empty_dialog()
            native.key(0x0D)  # Cancel is the default; Enter must preserve the item.
            native.wait_for(
                lambda: call(
                    lambda: (
                        not any(
                            isinstance(w, Gtk.MessageDialog) and w.get_visible()
                            for w in Gtk.Window.list_toplevels()
                        )
                    )
                ),
                "Default Cancel did not dismiss the confirmation",
            )
            count(1)
            assert len(inventory()) == 1
            report["cancel_preserved_items"] = True
            dialog, window = empty_dialog()

            def button_point():
                button = dialog.get_widget_for_response(Gtk.ResponseType.OK)
                position = button.translate_coordinates(
                    dialog,
                    button.get_allocated_width() // 2,
                    button.get_allocated_height() // 2,
                )
                assert position is not None
                return position

            bx, by = call(button_point)
            location = wintypes.POINT(bx, by)
            native.user.ClientToScreen.argtypes = (
                wintypes.HWND,
                ctypes.POINTER(wintypes.POINT),
            )
            assert native.user.ClientToScreen(window["hwnd"], ctypes.byref(location))
            native.click(location.x, location.y)
            count(0)
            assert inventory() == []
            time.sleep(0.5)
            capture("trash-empty")
            report.update(confirmed_empty=True, ok=True)
            file.unlink()
            root.rmdir()
            if args.exercise_internal:
                assert report["drop_protocols"] == ["ole2"], report["drop_protocols"]
                time.sleep(1)

                def drag_icon(source, target):
                    previous_ends = len(report.get("drag_end", []))
                    native.user.SetCursorPos(int(source[0]), int(source[1]))
                    time.sleep(0.3)
                    native.user.mouse_event(2, 0, 0, 0, 0)
                    try:
                        for step in range(1, 31):
                            native.user.SetCursorPos(
                                int(source[0] + (target[0] - source[0]) * step / 30),
                                int(source[1] + (target[1] - source[1]) * step / 30),
                            )
                            time.sleep(0.05)
                        time.sleep(0.5)
                    finally:
                        native.user.mouse_event(4, 0, 0, 0, 0)
                    native.wait_for(
                        lambda: len(report.get("drag_end", [])) > previous_ends,
                        "Native drag did not finish after releasing the mouse",
                    )

                # Exercise drag-off immediately after the external applet drop;
                # a previous successful drop must not suppress this new gesture.
                calendar = call(lambda: point(target_id="applet://calendar"))
                drag_icon(calendar, (calendar[0], 200))
                native.wait_for(
                    lambda: (
                        [
                            p["target"]
                            for p in json.loads(config.read_text(encoding="utf-8"))[
                                "pinned"
                            ]
                        ]
                        == ["applet://trash", "applet://clock"]
                    ),
                    "Drag-off inherited the preceding external drop's commit",
                )
                report["unpin_after_external_drop"] = True
                time.sleep(0.5)
                source = call(lambda: point(target_id="applet://clock"))
                destination = call(point)
                drag_icon(source, (destination[0] - 15, destination[1]))
                native.wait_for(
                    lambda: (
                        [
                            p["target"]
                            for p in json.loads(config.read_text(encoding="utf-8"))[
                                "pinned"
                            ]
                        ]
                        == ["applet://clock", "applet://trash"]
                    ),
                    "Native same-widget drag did not persist a reordered dock",
                )
                report["internal_reorder"] = True
                time.sleep(0.5)
                capture("internal-reordered")
                source = call(lambda: point(target_id="applet://clock"))
                drag_icon(source, (source[0], 200))
                native.wait_for(
                    lambda: (
                        [
                            p["target"]
                            for p in json.loads(config.read_text(encoding="utf-8"))[
                                "pinned"
                            ]
                        ]
                        == ["applet://trash"]
                    ),
                    "Native drag-off did not unpin Clock",
                )
                report["internal_unpin"] = True
                time.sleep(0.5)
                capture("internal-unpinned")
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            report["failure_windows"] = native.windows()
            capture("failure")
        finally:
            for window in owned:
                if any(
                    w["hwnd"] == window["hwnd"] and w["pid"] == window["pid"]
                    for w in native.windows()
                ):
                    native.user.PostMessageW(window["hwnd"], 0x10, 0, 0)
            if ready.is_set():

                def close():
                    for window in Gtk.Window.list_toplevels():
                        if isinstance(window, Gtk.MessageDialog):
                            window.response(Gtk.ResponseType.CANCEL)
                    grab = Gtk.grab_get_current()
                    if isinstance(grab, Gtk.MenuShell):
                        grab.cancel()
                    shared["ui"].window.close()

                call(close)

    driver = threading.Thread(target=drive, name="windows-trash-smoke", daemon=True)
    driver.start()
    try:
        app.main()
        driver.join(10)
        assert not driver.is_alive()
        assert native.workarea() == baseline
        if "observer" in shared:
            observer = shared["observer"]
            assert observer._closed and not observer._registration
            assert observer._hwnd is None and not observer._pidl.value
            assert not native.user.IsWindow(report["native_monitor"]["hwnd"])
            report["native_monitor_closed"] = True
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
