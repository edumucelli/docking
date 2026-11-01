"""Verify one real static/minimized capture serves multiple preview sizes."""

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
        parser.error("Requires an owned Windows desktop")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(output / "config")
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib
    from PIL import ImageGrab

    import docking.app as app
    from docking.platform.backends.base import ActionResult
    from docking.platform.native.windows.capture import WindowsCaptureStream
    from docking.platform.native.windows.ffi import bind
    from tools.windows.visual.desktop_driver import WindowsDesktop, read_shared_json

    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline, explorer = native.workarea(), native.explorer()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [{"kind": "applet", "target": "applet://clock"}],
                "icon_size": 64,
                "hide_mode": "none",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        )
    )
    ready, shared, errors = threading.Event(), {}, []
    report = {"pid": os.getpid(), "reads": [], "sizes": []}
    original_build, original_read = app.build_dock_window, WindowsCaptureStream.read

    def build(**kwargs):
        ui = original_build(**kwargs)
        shared.update(ui=ui, backend=kwargs["session_backend"])
        ready.set()
        return ui

    def read(stream, timeout=0.3):
        pixels = original_read(stream, timeout=timeout)
        report["reads"].append(
            {
                "hwnd": stream.hwnd,
                "pid": stream.pid,
                "pixels": None if pixels is None else [pixels.width, pixels.height],
            }
        )
        return pixels

    app.build_dock_window, WindowsCaptureStream.read = build, read

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
        assert done.wait(30), "GTK did not service an observation"
        if failures:
            raise failures[0]
        return values[0]

    def publish():
        (output / "result.json").write_text(json.dumps(report, indent=2))

    def drive():
        dock, fixture = None, None
        try:
            assert ready.wait(90)
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
            native.user.SetCursorPos(1100, 100)
            folder = output / "fixture"
            folder.mkdir()
            with (folder / "stderr.log").open("w") as log:
                fixture = subprocess.Popen(
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

            def state():
                try:
                    return read_shared_json(folder / "state.json")
                except (FileNotFoundError, json.JSONDecodeError):
                    return None

            initial = native.wait_for(state, "Fixture did not map", 30)
            target = initial["windows"]["Target"]
            windows, previews = shared["backend"].windows, shared["backend"].previews
            selected = native.wait_for(
                lambda: call(
                    lambda: next(
                        (
                            w
                            for w in windows.list_all_windows()
                            if w.id.value == target and w.pid == fixture.pid
                        ),
                        None,
                    )
                ),
                "Fixture not tracked",
            )
            report.update(fixture_pid=fixture.pid, target=target)

            def capture(width, height):
                return call(
                    lambda: previews.capture(selected.id, width=width, height=height)
                )

            warm = native.wait_for(
                lambda: capture(320, 200), "Warm capture did not arrive", 20
            )
            warm.image.savev(str(output / "warm.png"), "png", [], [])
            show = bind(
                native.user,
                "ShowWindowAsync",
                ctypes.c_int32,
                ctypes.c_void_p,
                ctypes.c_int32,
            )
            assert show(target, 6)
            native.wait_for(
                lambda: call(
                    lambda: (
                        next(
                            w for w in windows.list_all_windows() if w.id == selected.id
                        ).minimized
                    )
                ),
                "Native minimize did not settle",
            )
            report["minimized"] = True
            first = len(report["reads"])
            deadline = time.monotonic() + 8
            while not any(
                v["hwnd"] == target and v["pixels"] is None
                for v in report["reads"][first:]
            ):
                assert time.monotonic() < deadline, (
                    "Static stream did not exhaust its buffered frames"
                )
                capture(320, 200)
                time.sleep(0.2)
            report["exhausted_buffer"] = True
            publish()
            for width, height in ((190, 130), (80, 80), (640, 400)):
                image = native.wait_for(
                    lambda w=width, h=height: capture(w, h),
                    "Static minimized source lost its preview at a new size",
                    5,
                )
                assert image.width <= width and image.height <= height
                assert image.width > 20 and image.height > 20
                image.image.savev(
                    str(output / f"preview-{width}x{height}.png"), "png", [], []
                )
                report["sizes"].append(
                    {"request": [width, height], "actual": [image.width, image.height]}
                )
                publish()
            assert capture(320, 200) is not None
            for row in call(lambda: tuple(windows.list_all_windows())):
                if row.pid == fixture.pid:
                    assert (
                        call(lambda value=row.id: windows.close(value))
                        is ActionResult.OK
                    )
            assert fixture.wait(15) == 0
            assert capture(190, 130) is None
            report.update(ok=True, fixture_exit=0)
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            if fixture is not None and fixture.poll() is None:
                for window in native.windows(fixture.pid, include_cloaked=True):
                    native.user.PostMessageW(window["hwnd"], 0x10, 0, 0)
                try:
                    report["fixture_cleanup_exit"] = fixture.wait(15)
                except subprocess.TimeoutExpired:
                    fixture.kill()
                    fixture.wait(10)
                    report["forced_fixture_cleanup"] = True
            if dock is not None:
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            publish()

    driver = threading.Thread(
        target=drive, name="windows-preview-cache-smoke", daemon=False
    )
    driver.start()
    try:
        app.main()
        driver.join(30)
        assert not driver.is_alive()
        assert native.workarea() == baseline and native.explorer() == explorer
        report.update(normal_shutdown=True, workarea_after=baseline)
        if errors:
            raise errors[0]
    finally:
        WindowsCaptureStream.read = original_read
        publish()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
