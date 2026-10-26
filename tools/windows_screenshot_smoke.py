"""Verify full/window/region screenshots using real pixels, input and clipboard.

The separate Color Picker fixture supplies six known colors. System.Windows.Forms
reads the actual Windows clipboard, independently of GTK and the capture API.
"""

from __future__ import annotations

import argparse
import base64
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
    shutdown = parser.add_mutually_exclusive_group()
    shutdown.add_argument("--shutdown-region", action="store_true")
    shutdown.add_argument("--shutdown-window", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires an owned Windows desktop")
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

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import Image, ImageGrab

    import docking.app as app
    from docking.applets.screenshot import preview as screenshot_preview
    from docking.applets.screenshot.windows import (
        WindowsScreenshotAPI,
        WindowsScreenshotBackend,
    )
    from tools.windows_colorpicker_smoke import COLORS
    from tools.windows_desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline, explorer = native.workarea(), native.explorer()
    native.user.SetCursorPos(1100, 40)
    ready, shared, errors = threading.Event(), {}, []
    report = {"pid": os.getpid(), "stages": [], "captures": []}
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://screenshot"},
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
    from docking.platform.backends.windows.menus import WindowsMenuSession

    observe = WindowsMenuSession._observe_foreground

    def observed(session, identity):
        report.setdefault("foreground_events", []).append(
            {
                "identity": identity,
                "time": time.monotonic(),
                "thread": threading.get_ident(),
            }
        )
        observe(session, identity)

    WindowsMenuSession._observe_foreground = observed
    from docking.platform.backends.windows.capture import (
        WindowsCaptureDevice,
        WindowsCaptureStream,
    )

    held_frame, window_done = threading.Event(), threading.Event()
    stream_read = WindowsCaptureStream.read
    stream_close, device_close = WindowsCaptureStream.close, WindowsCaptureDevice.close

    def read(stream, timeout=0.3):
        pixels = stream_read(stream, timeout)
        if args.shutdown_window and pixels is not None:
            held_frame.set()
            assert shared["window_cancelled"].wait(20), (
                "Stop did not cancel native read"
            )
        return pixels

    def closed(kind, original, resource):
        original(resource)
        report.setdefault("resource_closes", []).append(
            {"kind": kind, "thread": threading.get_ident()}
        )

    WindowsCaptureStream.read = read
    WindowsCaptureStream.close = lambda stream: closed("stream", stream_close, stream)
    WindowsCaptureDevice.close = lambda device: closed("device", device_close, device)
    save_response, save_png = (
        screenshot_preview.ScreenshotPreview._save_response,
        screenshot_preview.save_png,
    )

    def response(view, chooser, value):
        report["save_response"] = {
            "response": int(value),
            "filename": chooser.get_filename(),
        }
        return save_response(view, chooser, value)

    def save(image, path):
        report["export_path"] = str(path)
        return save_png(image, path)

    screenshot_preview.ScreenshotPreview._save_response = response
    screenshot_preview.save_png = save
    original_build = app.build_dock_window
    original_desktop, original_window = (
        WindowsScreenshotAPI.desktop,
        WindowsScreenshotAPI.window,
    )

    def desktop(api, region=None):
        report["captures"].append(
            {
                "mode": "region" if region else "full",
                "region": [region.x, region.y, region.width, region.height]
                if region
                else None,
                "time": time.monotonic(),
                "thread": threading.get_ident(),
            }
        )
        return original_desktop(api, region)

    def window(api, cancelled, *, excluded=(), source=None):
        shared["window_cancelled"] = cancelled
        report["captures"].append(
            {
                "mode": "window",
                "time": time.monotonic(),
                "thread": threading.get_ident(),
            }
        )
        report["window_source"] = (
            None if source is None else {"hwnd": source.id.value, "pid": source.pid}
        )
        foreground = native.user.GetForegroundWindow()
        report["window_foreground"] = next(
            (
                {"hwnd": w["hwnd"], "pid": w["pid"], "class": w["class"]}
                for w in native.windows()
                if w["hwnd"] == foreground
            ),
            {"hwnd": foreground},
        )
        try:
            result = original_window(api, cancelled, excluded=excluded, source=source)
        except Exception as exc:
            report["window_error"] = repr(exc)
            raise
        finally:
            window_done.set()
        report["window_returned_image"] = result is not None
        return result

    WindowsScreenshotAPI.desktop, WindowsScreenshotAPI.window = desktop, window

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
        assert done.wait(20), "GTK did not service the observation"
        if failures:
            raise failures[0]
        return values[0]

    def publish():
        (output / "result.json").write_text(json.dumps(report, indent=2))

    def stage(name):
        report["stages"].append(name)
        publish()

    def clipboard(points):
        positions = ",".join(
            "[Drawing.Point]::new(" + str(x) + "," + str(y) + ")" for x, y in points
        )
        command = (
            """[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
Add-Type -AssemblyName System.Windows.Forms
$i=[Windows.Forms.Clipboard]::GetImage();if($null -eq $i){throw 'No clipboard image'}
try {$rows=@();foreach($p in @("""
            + positions
            + """)){$c=$i.GetPixel($p.X,$p.Y)
$rows+=,@([int]$c.R,[int]$c.G,[int]$c.B)}
@{width=$i.Width;height=$i.Height;pixels=$rows}|ConvertTo-Json -Compress -Depth 5
}finally{$i.Dispose()}"""
        )
        value = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-EncodedCommand",
                base64.b64encode(command.encode("utf-16-le")).decode(),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert value.returncode == 0, value.stderr[-1000:]
        return json.loads(value.stdout)

    def drive():
        dock = child = None
        try:
            assert ready.wait(90)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock missing",
            )
            applet = call(lambda: shared["model"].get_applet("applet://screenshot"))
            shared["applet"] = applet
            assert isinstance(applet._backend, WindowsScreenshotBackend)
            native.wait_for(
                lambda: call(lambda: applet._available), "Screenshot unavailable"
            )
            assert "docking.applets.screenshot.linux" not in sys.modules
            folder = output / "fixture"
            folder.mkdir()
            child = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("windows_colorpicker_smoke.py")),
                    "--output",
                    str(folder),
                    "--fixture",
                ],
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
                "Color fixture missing",
                30,
            )
            left, top, right, bottom = source["rect"]
            width, height = right - left, bottom - top
            centers = [
                (int((i % 3 + 0.5) * width / 3), int((i // 3 + 0.5) * height / 2))
                for i in range(6)
            ]
            screen_centers = [(left + x, top + y) for x, y in centers]
            report["fixture"] = source

            def point():
                dock_window = shared["ui"].window
                frame = dock_window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://screenshot"
                )
                bounds = next(
                    w["rect"]
                    for w in native.windows(os.getpid())
                    if w["hwnd"] == dock["hwnd"]
                )
                scale = dock_window.get_scale_factor()
                return bounds[0] + (rect.x + rect.w / 2) * scale, bounds[1] + (
                    rect.y + rect.h / 2
                ) * scale

            def click(right=False):
                native.user.SetCursorPos(1100, 40)
                time.sleep(0.6)
                p = call(point)
                assert native.window_at(*p) == dock["hwnd"]
                native.click(*p, right=right)

            def context():
                anchor = applet.popup_anchor
                parent = anchor.parent if anchor is not None else None
                surface = shared["ui"].window.surface_service
                session = surface._menus
                return {
                    "anchor": anchor is not None,
                    "parent_is_dock": parent is shared["ui"].window,
                    "session": None
                    if session is None
                    else {
                        "previous": session._previous,
                        "last": session._last_foreground,
                        "parent": session._parent_identity,
                        "in_session": session._in_session,
                        "source": session.capture_source(),
                    },
                }

            def menu(index):
                report.setdefault("menu_contexts", []).append({"before": call(context)})
                click(right=True)
                native.wait_for(
                    lambda: call(lambda: isinstance(Gtk.grab_get_current(), Gtk.Menu)),
                    "Menu missing",
                )
                report["menu_contexts"][-1]["open"] = call(context)
                native.key(0x24)
                for _ in range(index):
                    native.key(0x28)
                native.key(0x0D)

            def preview(previous=None):
                return native.wait_for(
                    lambda: call(
                        lambda: (
                            applet._preview
                            if applet._preview is not previous
                            and applet._preview is not None
                            and not applet._preview._closed
                            else None
                        )
                    ),
                    "No captured preview",
                    20,
                )

            def close_preview(value):
                call(value.close)
                time.sleep(0.4)

            menu(2)
            native.wait_for(
                lambda: call(
                    lambda: (
                        applet._selection is not None
                        and applet._selection.window is not None
                    )
                ),
                "First-menu region overlay missing",
            )
            assert call(lambda: applet.popup_anchor.parent is shared["ui"].window)
            native.key(0x1B)
            native.wait_for(
                lambda: call(lambda: applet._selection.window is None),
                "First-menu cancel failed",
            )
            stage("first-interaction-menu-parent-and-cancel")
            click()
            current = preview()
            full = clipboard(screen_centers)
            assert (full["width"], full["height"]) == (1280, 800)
            assert full["pixels"] == [list(c) for c in COLORS]
            report["full"] = full
            native.user.SetCursorPos(1100, 40)
            time.sleep(
                0.8
            )  # Let dock magnification settle before clicking the preview.
            ImageGrab.grab(all_screens=True).save(output / "full-preview.png")
            stage("native-full-click-pixels-clipboard")

            # Open Save As using the real GTK button, then type a UNC path.
            button = call(
                lambda: current.dialog.get_widget_for_response(Gtk.ResponseType.ACCEPT)
            )

            def button_point():
                dialog = current.dialog
                x, y = button.translate_coordinates(dialog, 0, 0)
                success, ox, oy = dialog.get_window().get_origin()
                assert success
                allocation, scale = button.get_allocation(), dialog.get_scale_factor()
                # GDK origins are logical root coordinates on this baseline.
                return (ox + x + allocation.width / 2) * scale, (
                    oy + y + allocation.height / 2
                ) * scale

            save_point = call(button_point)
            target = native.window_at(*save_point)
            assert target != dock["hwnd"], "Magnified dock still covers Save As"
            native.click(*save_point)
            native.wait_for(
                lambda: call(lambda: current._chooser is not None),
                "Save chooser missing",
            )
            chooser_window = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows(os.getpid())
                        if w["class"] == "#32770" and w["title"] == "Screenshot"
                    ),
                    None,
                ),
                "Native save dialog did not map",
            )
            native.wait_for(
                lambda: native.user.GetForegroundWindow() == chooser_window["hwnd"],
                "Native save dialog did not receive foreground",
            )
            report["save_dialog"] = {
                "class": chooser_window["class"],
                "hwnd": chooser_window["hwnd"],
            }
            saved = output / "Screenshot ü.png"
            native.key(ord("N"), alt=True)
            native.key(ord("A"), control=True)
            native.type_text(str(saved))
            native.key(0x0D)
            time.sleep(0.5)
            if not saved.exists() and call(lambda: current._chooser is not None):
                native.key(0x0D)
            native.wait_for(saved.exists, "PNG was not saved", 20)
            with Image.open(saved) as exported:
                assert exported.size == (1280, 800)
                assert [list(exported.getpixel(p))[:3] for p in screen_centers] == [
                    list(c) for c in COLORS
                ]
            stage("native-save-as-unc-unicode-png")
            close_preview(current)

            native.click(left + 20, top + 20)
            native.wait_for(
                lambda: native.user.GetForegroundWindow() == source["hwnd"],
                "Source lost foreground",
            )
            menu(1)
            if args.shutdown_window:
                assert held_frame.wait(20), "No real native frame reached the barrier"
                assert call(lambda: applet._pending)
                assert native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
                native.wait_for(
                    lambda: not native.user.IsWindow(dock["hwnd"]),
                    "Dock did not close during capture",
                )
                assert window_done.wait(20), "Native capture did not finish closing"
                assert applet._preview is None or applet._preview._closed
                stage("normal-close-during-native-window-read")
                report["ok"] = True
                return
            current = preview(current)
            captured = clipboard(centers)
            assert (captured["width"], captured["height"]) == (width, height)
            assert captured["pixels"] == [list(c) for c in COLORS]
            report["window"] = captured
            native.user.SetCursorPos(1100, 40)
            time.sleep(0.8)
            ImageGrab.grab(all_screens=True).save(output / "window-preview.png")
            stage("native-window-menu-source-resolution-pixels")
            close_preview(current)

            menu(2)
            native.wait_for(
                lambda: call(
                    lambda: (
                        applet._selection is not None
                        and applet._selection.window is not None
                    )
                ),
                "Region overlay missing",
            )
            start = (left + width // 3 - 41, top + 21)
            end = (start[0] + 80, start[1] + 60)
            native.drag(start, end)
            current = preview(current)
            cropped = clipboard([(10, 10), (40, 10), (41, 10), (70, 50)])
            assert (cropped["width"], cropped["height"]) == (80, 60)
            assert cropped["pixels"] == [
                list(COLORS[0]),
                list(COLORS[0]),
                list(COLORS[1]),
                list(COLORS[1]),
            ]
            assert report["captures"][-1]["region"] == [start[0], start[1], 80, 60]
            report["region"] = cropped
            stage("native-region-drag-exact-bounds-pixels")
            close_preview(current)

            report["clipboard_after_region_preview_close"] = clipboard([(10, 10)])
            before = len(report["captures"])
            menu(2)
            native.wait_for(
                lambda: call(
                    lambda: (
                        applet._selection is not None
                        and applet._selection.window is not None
                    )
                ),
                "Cancel overlay missing",
            )
            report["clipboard_during_cancel_overlay"] = clipboard([(10, 10)])
            native.key(0x1B)
            native.wait_for(
                lambda: call(lambda: applet._selection.window is None),
                "Escape did not cancel",
            )
            assert len(report["captures"]) == before
            assert clipboard([(10, 10)])["width"] == 80
            stage("native-region-escape-no-capture")

            began = time.monotonic()
            menu(3)
            native.wait_for(
                lambda: call(lambda: applet._delay_source != 0), "Delay not scheduled"
            )
            time.sleep(1)
            assert len(report["captures"]) == before
            current = preview(current)
            assert report["captures"][-1]["time"] - began >= 2.8
            assert clipboard(screen_centers)["pixels"] == [list(c) for c in COLORS]
            stage("native-three-second-delayed-full")
            close_preview(current)

            before = len(report["captures"])
            menu(2 if args.shutdown_region else 6)
            if args.shutdown_region:
                native.wait_for(
                    lambda: call(
                        lambda: (
                            applet._selection is not None
                            and applet._selection.window is not None
                        )
                    ),
                    "Shutdown overlay missing",
                )
            else:
                native.wait_for(
                    lambda: call(lambda: applet._delay_source != 0),
                    "Shutdown delay missing",
                )
            assert native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            native.wait_for(
                lambda: not native.user.IsWindow(dock["hwnd"]), "Dock did not close"
            )
            assert len(report["captures"]) == before
            stage(
                "normal-close-cancels-selection"
                if args.shutdown_region
                else "normal-close-cancels-delay"
            )
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            for row in native.windows(os.getpid()):
                if row["class"] == "#32770" and row["title"] == "Screenshot":
                    native.user.PostMessageW(row["hwnd"], 0x10, 0, 0)
            try:
                native.wait_for(
                    lambda: (
                        not any(
                            w["class"] == "#32770" for w in native.windows(os.getpid())
                        )
                    ),
                    "Native save dialog did not close",
                    3,
                )
            except AssertionError:
                report["native_dialog_cleanup_failed"] = True
            for row in native.windows(os.getpid()):
                if row["title"] == "Screenshot" and row["class"] == "gdkWindowToplevel":
                    native.user.PostMessageW(row["hwnd"], 0x10, 0, 0)
            if child is not None and child.poll() is None:
                for row in native.windows(child.pid):
                    native.user.PostMessageW(row["hwnd"], 0x10, 0, 0)
                try:
                    report["fixture_exit"] = child.wait(15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(10)
                    report["forced_fixture_cleanup"] = True
            if dock is not None and native.user.IsWindow(dock["hwnd"]):
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            publish()

    driver = threading.Thread(
        target=drive, name="windows-screenshot-smoke", daemon=False
    )
    driver.start()
    try:
        app.main()
        driver.join(40)
        assert not driver.is_alive()
        applet = shared["applet"]
        assert applet._delay_source == 0 and applet._stopped
        assert native.workarea() == baseline and native.explorer() == explorer
        assert report.get("fixture_exit") == 0 and not report.get(
            "forced_fixture_cleanup"
        )
        assert all(v["thread"] != threading.get_ident() for v in report["captures"])
        worker = next(
            (v["thread"] for v in report["captures"] if v["mode"] == "window"), None
        )
        assert errors or [
            v["kind"] for v in report["resource_closes"] if v["thread"] == worker
        ] == ["stream", "device"]
        report.update(normal_shutdown=True, workarea_after=baseline)
        if errors:
            raise errors[0]
    finally:
        app.build_dock_window = original_build
        screenshot_preview.ScreenshotPreview._save_response = save_response
        screenshot_preview.save_png = save_png
        WindowsMenuSession._observe_foreground = observe
        WindowsCaptureStream.read = stream_read
        WindowsCaptureStream.close, WindowsCaptureDevice.close = (
            stream_close,
            device_close,
        )
        WindowsScreenshotAPI.desktop, WindowsScreenshotAPI.window = (
            original_desktop,
            original_window,
        )
        publish()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
