"""Keep native hung-window identity, actions and previews through ghost recovery.

The owned Win32 fixture explicitly assigns Calculator's registered application
ID to its two test windows, exercising the real common matching/grouping path.
It does not launch or impersonate a Calculator process. All HWNDs, ghost
associations, native actions and captured pixels come from the actual desktop.
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
    parser.add_argument("--physical-input", action="store_true")
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
    from gi.repository import GLib
    from PIL import ImageGrab

    import docking.app as app
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.backends.base import ActionResult, Rect
    from docking.platform.backends.windows.ffi import bind
    from tools.windows_desktop_driver import WindowsDesktop, read_shared_json

    native = WindowsDesktop()
    assert not any(w["title"] in {"Docking", "Calculator"} for w in native.windows())
    baseline, explorer = native.workarea(), native.explorer()
    registry = ApplicationRegistry()
    registry.refresh()
    application_id = next(
        info.desktop_id
        for info in registry.snapshot()
        if info.desktop_id.casefold()
        == "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App".casefold()
    )
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "app", "target": application_id},
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
    ghost_for = bind(
        native.user, "GhostWindowFromHungWindow", ctypes.c_void_p, ctypes.c_void_p
    )
    original_for = bind(
        native.user, "HungWindowFromGhostWindow", ctypes.c_void_p, ctypes.c_void_p
    )
    show = bind(
        native.user, "ShowWindowAsync", ctypes.c_int32, ctypes.c_void_p, ctypes.c_int32
    )
    bind(native.user, "IsIconic", ctypes.c_int32, ctypes.c_void_p)
    ready, shared, errors = threading.Event(), {}, []
    report = {
        "pid": os.getpid(),
        "fixture_application_id": application_id,
        "stages": {},
        "actions": [],
    }
    original = app.build_dock_window

    def build(**kwargs):
        ui = original(**kwargs)
        shared.update(ui=ui, backend=kwargs["session_backend"], model=kwargs["model"])
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
        assert done.wait(30), "GTK did not service an observation"
        if failures:
            raise failures[0]
        return values[0]

    def publish():
        (output / "result.json").write_text(json.dumps(report, indent=2))

    def drive():
        dock, fixture, fixture_dir = None, None, output / "fixture"
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
            time.sleep(2)
            windows, previews = shared["backend"].windows, shared["backend"].previews
            original_perform = windows._native.perform

            def observed_perform(window, action):
                ctypes.set_last_error(0)
                result = original_perform(window, action)
                report.setdefault("native_actions", []).append(
                    {
                        "id": window.id.value,
                        "action": action,
                        "result": result.value,
                        "error": ctypes.get_last_error(),
                    }
                )
                publish()
                return result

            call(lambda: setattr(windows._native, "perform", observed_perform))
            fixture_dir.mkdir()
            with (fixture_dir / "stderr.log").open("w") as log:
                fixture = subprocess.Popen(
                    [
                        sys.executable,
                        str(
                            Path(__file__).with_name("windows_windowpicker_fixture.py")
                        ),
                        "--output",
                        str(fixture_dir),
                        "--app-ids",
                    ],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=log,
                )

            def state():
                try:
                    return read_shared_json(fixture_dir / "state.json")
                except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                    return None

            initial = native.wait_for(state, "Fixture did not map", 30)
            ids = set(initial["windows"].values())
            target = initial["windows"]["Target"]
            sibling = initial["windows"]["Sibling"]
            for label in initial["windows"]:
                request = fixture_dir / ("identity-" + label + ".request.json")
                temporary = request.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps({"window": label, "app_id": application_id})
                )
                temporary.rename(request)
            native.wait_for(
                lambda: (
                    (current := state()) is not None
                    and set(current["app_ids"].values()) == {application_id}
                ),
                "Fixture did not set its native application identity",
            )

            def rows():
                return call(lambda: tuple(windows.list_windows(application_id)))

            def grouped():
                result = rows()
                return (
                    result
                    if len(result) == 2 and {w.id.value for w in result} == ids
                    else None
                )

            originals = native.wait_for(
                grouped, "Fixture did not group into two windows"
            )
            by_id = {w.id.value: w for w in originals}
            selected = by_id[target]
            report.update(fixture_pid=fixture.pid, original_ids=sorted(ids))

            def record(label):
                values = rows()
                assert len(values) == 2 and {w.id.value for w in values} == ids
                assert all(
                    w.pid == fixture.pid and w.app_id == application_id for w in values
                )
                assert not any(
                    w.wm_class == "Ghost"
                    for w in call(lambda: tuple(windows.list_all_windows()))
                )
                item = call(
                    lambda: shared["model"].find_by_desktop_id(
                        desktop_id=application_id
                    )
                )
                assert item is not None and item.is_running and item.instance_count == 2
                report["stages"][label] = [
                    {
                        "id": w.id.value,
                        "pid": w.pid,
                        "app_id": w.app_id,
                        "title": w.title,
                        "active": w.active,
                        "minimized": w.minimized,
                        "geometry": None
                        if w.geometry is None
                        else [
                            w.geometry.x,
                            w.geometry.y,
                            w.geometry.width,
                            w.geometry.height,
                        ],
                    }
                    for w in values
                ]
                publish()

            def preview(label):
                image = native.wait_for(
                    lambda: call(
                        lambda: previews.capture(selected.id, width=320, height=200)
                    ),
                    "Native preview did not arrive",
                    20,
                )
                image.image.savev(str(output / (label + ".png")), "png", [], [])
                return image

            def hover(label):
                def point():
                    window = shared["ui"].window
                    frame = window.geometry.build_frame(main_cursor=-1.0)
                    rect = next(
                        g.draw_rect
                        for g in frame.item_geometries
                        if g.item.desktop_id == application_id
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

                x, y = call(point)
                native.user.SetCursorPos(int(x), int(y))
                time.sleep(1.5)
                ImageGrab.grab(all_screens=True).save(output / (label + ".png"))

            def pointer(name, start, end):
                if args.physical_input:
                    request = output / (name + "-request.json")
                    temporary = request.with_suffix(".tmp")
                    temporary.write_text(json.dumps({"start": start, "end": end}))
                    temporary.rename(request)
                    native.wait_for(
                        lambda: (output / (name + "-complete.json")).exists(),
                        "VM virtual-pointer driver did not complete",
                        60,
                    )
                elif start == end:
                    native.click(*start)
                else:
                    native.drag(start, end)

            record("responsive")
            preview("responsive-preview")
            hover("responsive-desktop")
            native.click(300, 120)
            native.wait_for(
                lambda: native.user.GetForegroundWindow() == target,
                "Fixture did not receive native focus",
            )
            assert show(sibling, 6)
            native.wait_for(
                lambda: native.user.IsIconic(sibling),
                "Could not establish the pre-minimized sibling fixture",
            )
            report["pre_minimized_sibling"] = sibling
            (fixture_dir / "hang.request").touch()
            native.wait_for(
                lambda: (current := state()) is not None and current.get("hung"),
                "Fixture did not hang",
            )
            time.sleep(6)
            native.click(150, 95)
            ghost = native.wait_for(
                lambda: ghost_for(target), "No real DWM ghost appeared", 30
            )
            assert original_for(ghost) == target
            surface = next(w for w in native.windows() if w["hwnd"] == ghost)
            assert surface["class"] == "Ghost" and surface["pid"] != fixture.pid
            report["ghost"] = surface
            native.wait_for(grouped, "Ghosts changed the application window group")
            record("hung")
            ghost_target = call(
                lambda: windows.capture_target(selected.id, fixture.pid, application_id)
            )
            assert ghost_target.hwnd == ghost and ghost_target.pid == surface["pid"]
            assert (
                call(lambda: previews.capture(selected.id, width=320, height=200))
                is None
            )
            preview("hung-preview")
            assert call(lambda: windows.minimize_all(application_id)) is ActionResult.OK
            native.wait_for(
                lambda: native.user.IsIconic(ghost) and native.user.IsIconic(sibling),
                "Ghost minimize did not reach native surfaces",
            )
            native.wait_for(
                lambda: all(w.minimized for w in rows()),
                "Ghost minimized tracking did not settle",
            )
            record("minimized")
            hover("minimized-desktop")

            def thumbnail_point():
                popup = shared["ui"].window.preview
                if (
                    not popup.get_visible()
                    or selected.id not in popup._thumbnail_images
                ):
                    return None
                image, *_rest = popup._thumbnail_images[selected.id]
                origin = popup.get_window().get_origin()[-2:]
                offset = image.translate_coordinates(popup, 0, 0)
                allocation = image.get_allocation()
                scale = popup.get_scale_factor()
                return (
                    (origin[0] + offset[0] + allocation.width / 2) * scale,
                    (origin[1] + offset[1] + allocation.height / 2) * scale,
                )

            native.click(
                *native.wait_for(
                    lambda: call(thumbnail_point), "Minimized thumbnail did not map"
                )
            )
            native.wait_for(
                lambda: (
                    not native.user.IsIconic(ghost)
                    and native.user.GetForegroundWindow() == ghost
                ),
                "Ghost did not restore/activate",
            )
            assert native.user.IsIconic(sibling)
            caption = next(w["rect"] for w in native.windows() if w["hwnd"] == ghost)
            start = (caption[0] + 150, caption[1] + 15)
            assert native.window_at(*start) == ghost
            end = (start[0] + 100, start[1] + 40)
            pointer("pointer", start, end)

            def moved_bounds():
                bounds = next(w["rect"] for w in native.windows() if w["hwnd"] == ghost)
                return (
                    bounds
                    if bounds[0] != caption[0] and bounds[1] != caption[1]
                    else None
                )

            bounds = native.wait_for(moved_bounds, "Native ghost did not move", 15)
            expected = Rect(
                bounds[0], bounds[1], bounds[2] - bounds[0], bounds[3] - bounds[1]
            )
            native.wait_for(
                lambda: (
                    next(w for w in rows() if w.id.value == target).geometry == expected
                ),
                "Tracking did not use moved ghost bounds",
            )
            record("moved")
            hover("hung-desktop")
            report["actions"].extend(
                ["ghosts-minimized", "target-ghost-restored", "ghost-bounds-tracked"]
            )
            before_close = {w["hwnd"] for w in native.windows()}
            assert call(lambda: windows.close(selected.id)) is ActionResult.OK
            prompt = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows()
                        if w["hwnd"] not in before_close
                        and w["class"] == "#32770"
                        and w["title"] == "Python"
                    ),
                    None,
                ),
                "Ghost close did not open the Windows unresponsive-app prompt",
                20,
            )
            time.sleep(0.75)  # Let the native task dialog finish its entry animation.
            ImageGrab.grab(all_screens=True).save(output / "native-close-prompt.png")
            left, _top, right, bottom = prompt["rect"]
            wait_button = ((left + right) // 2, bottom - 38)
            pointer("close-pointer", wait_button, wait_button)
            native.wait_for(
                lambda: not any(w["hwnd"] == prompt["hwnd"] for w in native.windows()),
                "Wait did not dismiss the native close prompt",
            )
            assert fixture.poll() is None
            report["close_prompt"] = prompt
            report["actions"].append("native-close-prompt-wait-selected")
            (fixture_dir / "resume.request").touch()
            native.wait_for(
                lambda: (current := state()) is not None and current.get("recovered"),
                "Fixture did not resume",
            )
            native.wait_for(
                lambda: not any(ghost_for(value) for value in ids),
                "Ghosts did not retire after recovery",
                30,
            )
            native.wait_for(grouped, "Recovery lost original window IDs")
            assert not call(
                lambda: windows.capture_is_current(
                    selected.id, fixture.pid, application_id, ghost_target
                )
            )
            assert (
                call(lambda: previews.capture(selected.id, width=320, height=200))
                is None
            )
            preview("recovered-preview")
            record("recovered")
            hover("recovered-desktop")

            def recovered_thumbnails():
                popup = shared["ui"].window.preview
                if not popup.get_visible():
                    return None
                rendered = []
                for window in originals:
                    if window.id not in popup._thumbnail_images:
                        return None
                    image, width, height, preview = popup._thumbnail_images[window.id]
                    if preview is None or image.get_pixbuf() is None:
                        return None
                    rendered.append(
                        (window.id.value, width, height, image.get_pixbuf())
                    )
                return rendered

            rendered = native.wait_for(
                lambda: call(recovered_thumbnails),
                "Recovered hover kept an icon fallback instead of captured pixels",
                10,
            )
            report["recovered_thumbnails"] = []
            for value, width, height, pixbuf in rendered:
                pixbuf.savev(
                    str(output / f"recovered-hover-{value}.png"), "png", [], []
                )
                report["recovered_thumbnails"].append(
                    {
                        "id": value,
                        "request": [width, height],
                        "actual": [pixbuf.get_width(), pixbuf.get_height()],
                    }
                )
            ImageGrab.grab(all_screens=True).save(output / "recovered-desktop.png")
            for window in originals:
                assert (
                    call(lambda value=window.id: windows.close(value))
                    is ActionResult.OK
                )
            assert fixture.wait(15) == 0
            native.wait_for(
                lambda: not rows(), "Closed fixture retained running entries"
            )
            report.update(
                ok=True,
                fixture_exit=0,
                no_x11_imports=not any(
                    name.startswith("docking.platform.backends.x11")
                    for name in sys.modules
                ),
            )
            assert report["no_x11_imports"]
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc), native_windows=native.windows())
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            if fixture is not None and fixture.poll() is None:
                (fixture_dir / "resume.request").touch()
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

    driver = threading.Thread(target=drive, name="windows-ghost-smoke", daemon=False)
    driver.start()
    try:
        app.main()
        driver.join(30)
        assert not driver.is_alive()
        assert native.workarea() == baseline and native.explorer() == explorer
        report.update(normal_shutdown=True, workarea_after=baseline)
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
