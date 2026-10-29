"""Check fullscreen stacking through real Windows input, pixels and foreground.

Run on an idle desktop. Each scenario owns a complete dock and a separate GTK
fixture; F11 enters and exits fullscreen. The startup scenario opens fullscreen
before Docking starts, when the shell may omit both transition notifications.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
from ctypes import wintypes
from pathlib import Path

COLOR = (19, 47, 83)
TITLE = "Docking fullscreen stacking fixture"


def fixture(*, fullscreen: bool) -> None:
    from gi.repository import Gdk, GLib, Gtk

    window = Gtk.Window(title=TITLE)
    window.set_default_size(600, 380)
    window.move(60, 60)
    area = Gtk.DrawingArea()
    window.add(area)

    def draw(_widget, cr):
        cr.set_source_rgb(*(value / 255 for value in COLOR))
        cr.paint()
        return False

    def key(_window, event):
        if event.keyval not in (Gdk.KEY_F11, Gdk.KEY_Escape):
            return False
        active = window.get_window().get_state() & Gdk.WindowState.FULLSCREEN
        if active:
            window.unfullscreen()
        elif event.keyval == Gdk.KEY_F11:
            window.fullscreen()
        return True

    area.connect("draw", draw)
    window.connect("key-press-event", key)
    window.connect("destroy", Gtk.main_quit)
    if fullscreen:
        window.fullscreen()
    window.show_all()
    # Bound the lifetime even if the observing process stops unexpectedly.
    GLib.timeout_add_seconds(180, lambda: (window.destroy(), False)[1])
    Gtk.main()


def run_scenario(output: Path, scenario: str) -> None:
    from gi.repository import GLib
    from PIL import ImageGrab

    import docking.app as app
    from docking.platform.backends.windows.ffi import MonitorInfo, bind
    from tools.windows.visual.desktop_driver import WindowsDesktop
    from tools.windows.visual.smoke import dock_window

    native = WindowsDesktop()
    assert dock_window(native.windows()) is None, "Close other docks before checking"
    baseline, explorer = native.workarea(), native.explorer()
    assert explorer is not None, "Explorer must be running"
    report = {"scenario": scenario, "pid": os.getpid(), "cycles": []}
    shared, errors = {}, []
    ready = threading.Event()
    child = None
    style = bind(
        native.user, "GetWindowLongPtrW", ctypes.c_ssize_t, wintypes.HWND, ctypes.c_int
    )
    monitor_from_window = bind(
        native.user,
        "MonitorFromWindow",
        wintypes.HMONITOR,
        wintypes.HWND,
        wintypes.DWORD,
    )
    monitor_info = bind(
        native.user,
        "GetMonitorInfoW",
        wintypes.BOOL,
        wintypes.HMONITOR,
        ctypes.POINTER(MonitorInfo),
    )

    def publish():
        (output / "result.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )

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

    def fixture_window():
        return (
            next((w for w in native.windows(child.pid) if w["title"] == TITLE), None)
            if child is not None
            else None
        )

    def launch_fixture():
        nonlocal child
        child = subprocess.Popen(
            [
                sys.executable,
                __file__,
                "--output",
                str(output),
                "--scenario",
                scenario,
                "--fixture",
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        window = native.wait_for(fixture_window, "Fixture did not map", 40)
        left, top, right, bottom = window["rect"]
        native.click((left + right) // 2, (top + bottom) // 2)
        native.wait_for(
            lambda: native.user.GetForegroundWindow() == window["hwnd"],
            "Fixture did not acquire foreground",
        )
        info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        assert monitor_info(monitor_from_window(window["hwnd"], 2), ctypes.byref(info))
        bounds = info.monitor
        shared["monitor"] = [bounds.left, bounds.top, bounds.right, bounds.bottom]

    def close_fixture():
        window = fixture_window()
        if window is not None:
            native.user.PostMessageW(window["hwnd"], 0x10, 0, 0)
        if child is not None:
            try:
                report["fixture_exit"] = child.wait(10)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(5)
                report["fixture_forced"] = True
            if report.get("fixture_forced") or child.returncode != 0:
                errors.append(AssertionError("Fixture did not exit normally"))
                report["ok"] = False

    if scenario != "transition":
        try:
            launch_fixture()
            native.wait_for(
                lambda: fixture_window()["rect"] == shared["monitor"],
                "Fixture was not fullscreen before dock startup",
            )
        except Exception as exc:
            report.update(ok=False, error=repr(exc))
            close_fixture()
            publish()
            raise
    original = app.build_dock_window

    def build(**kwargs):
        ui = original(**kwargs)
        shared["ui"] = ui
        ready.set()
        return ui

    app.build_dock_window = build

    def drive():
        dock = None
        try:
            assert ready.wait(60), "Dock did not build"
            dock = native.wait_for(
                lambda: dock_window(native.windows(os.getpid())), "Dock did not map"
            )

            def clock_rectangle():
                window = shared["ui"].window
                frame = window.geometry.build_frame(main_cursor=-1.0)
                glyph = next(
                    row.draw_rect
                    for row in frame.item_geometries
                    if row.item.desktop_id == "applet://clock"
                )
                scale = window.get_scale_factor()
                bounds = next(
                    row["rect"]
                    for row in native.windows(os.getpid())
                    if row["hwnd"] == dock["hwnd"]
                )
                return [
                    int(bounds[0] + glyph.x * scale),
                    int(bounds[1] + glyph.y * scale),
                    int(bounds[0] + (glyph.x + glyph.w) * scale),
                    int(bounds[1] + (glyph.y + glyph.h) * scale),
                ]

            def observe():
                rectangle = call(clock_rectangle)

                def stacking():
                    surface = shared["ui"].window.surface_service
                    return {
                        "observer": surface._fullscreen_source,
                        "restoration_observer": surface._topmost_source,
                        "foreground_fullscreen": surface._appbar.fullscreen_active(
                            surface._mapping
                        ),
                        "gtk_state": int(shared["ui"].window.get_window().get_state()),
                    }

                image = ImageGrab.grab(all_screens=True).convert("RGB")
                crop = image.crop(tuple(rectangle))
                assert crop.width > 0 and crop.height > 0
                colors = crop.getcolors(crop.width * crop.height)
                return {
                    "dock_topmost": bool(style(dock["hwnd"], -20) & 8),
                    "stacking": call(stacking),
                    "fixture": fixture_window(),
                    "foreground": int(native.user.GetForegroundWindow()),
                    "clock_rectangle": rectangle,
                    "non_fixture_pixels": sum(
                        count for count, color in colors if color != COLOR
                    ),
                }

            if child is None:
                report["initial"] = observe()
                assert report["initial"]["dock_topmost"]
                assert report["initial"]["non_fixture_pixels"] > 0
                launch_fixture()

            def covered():
                state = observe()
                report["last_fullscreen_observation"] = state
                return (
                    state
                    if not state["dock_topmost"]
                    and state["non_fixture_pixels"] == 0
                    and state["foreground"] == state["fixture"]["hwnd"]
                    else None
                )

            for index in range(0 if scenario == "startup-close" else 2):
                if fixture_window()["rect"] != shared["monitor"]:
                    native.key(0x7A)  # F11.
                native.wait_for(
                    lambda: fixture_window()["rect"] == shared["monitor"],
                    "F11 did not enter fullscreen",
                )

                full = native.wait_for(covered, "Dock covered the fullscreen fixture")
                ImageGrab.grab(all_screens=True).save(
                    output / f"fullscreen-{index}.png"
                )
                native.key(0x7A)
                native.wait_for(
                    lambda: fixture_window()["rect"] != shared["monitor"],
                    "F11 did not leave fullscreen",
                )

                def restored():
                    state = observe()
                    report["last_windowed_observation"] = state
                    return (
                        state
                        if state["dock_topmost"]
                        and state["non_fixture_pixels"] > 0
                        and state["foreground"] == state["fixture"]["hwnd"]
                        else None
                    )

                normal = native.wait_for(
                    restored, "Dock did not recover after fullscreen"
                )
                ImageGrab.grab(all_screens=True).save(output / f"windowed-{index}.png")
                report["cycles"].append({"fullscreen": full, "windowed": normal})
                publish()
            if fixture_window()["rect"] != shared["monitor"]:
                native.key(0x7A)
            native.wait_for(
                lambda: fixture_window()["rect"] == shared["monitor"],
                "Fixture did not enter fullscreen before closing",
            )
            native.wait_for(covered, "Dock covered the fixture before native close")
            native.key(0x73, alt=True)  # Alt+F4 closes the actual fullscreen app.
            assert child.wait(10) == 0

            def after_close():
                state = observe()
                return (
                    state
                    if state["dock_topmost"]
                    and state["non_fixture_pixels"] > 0
                    and state["foreground"] != dock["hwnd"]
                    else None
                )

            report["closed_from_fullscreen"] = native.wait_for(
                after_close, "Dock did not recover when the fullscreen app closed"
            )
            native.wait_for(
                lambda: call(
                    lambda: (
                        shared["ui"].window.surface_service._fullscreen_source == 0
                        and shared["ui"].window.surface_service._topmost_source == 0
                    )
                ),
                "Fullscreen exit observer remained active",
            )
            report["fullscreen_observer_stopped"] = True
            ImageGrab.grab(all_screens=True).save(output / "fullscreen-closed.png")
            report["ok"] = True
        except Exception as exc:
            # Preserve unexpected failures while still closing both owned GUIs.
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            close_fixture()
            if dock is not None:
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            elif ready.is_set():
                call(lambda: shared["ui"].window.close())
            publish()

    driver = threading.Thread(
        target=drive, name="windows-fullscreen-smoke", daemon=False
    )
    driver.start()
    try:
        app.main()
        driver.join(30)
        assert not driver.is_alive()
        report["workarea_after"] = native.workarea()
        assert report["workarea_after"] == baseline
        assert native.explorer() == explorer
        if errors:
            raise errors[0]
        report["normal_shutdown"] = True
    except Exception as exc:
        report.update(ok=False, error=repr(exc))
        raise
    finally:
        publish()
        print(json.dumps(report, indent=2), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--scenario",
        choices=("all", "transition", "startup", "startup-close"),
        default="all",
    )
    parser.add_argument("--fixture", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This check requires an interactive Windows desktop")
    output = args.output.resolve()
    if not args.fixture:
        if output.exists() and any(output.iterdir()):
            parser.error("Use a fresh output directory")
        output.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo))
    if args.scenario == "all" and not args.fixture:
        results = {}
        for scenario in ("transition", "startup", "startup-close"):
            with (output / f"{scenario}.log").open("w", encoding="utf-8") as log:
                result = subprocess.run(
                    [
                        sys.executable,
                        __file__,
                        "--output",
                        str(output / scenario),
                        "--scenario",
                        scenario,
                    ],
                    cwd=repo,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=240,
                )
            results[scenario] = result.returncode
            (output / "result.json").write_text(
                json.dumps(
                    {
                        "scenarios": results,
                        "ok": results
                        == {"transition": 0, "startup": 0, "startup-close": 0},
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            assert result.returncode == 0, f"{scenario} failed; see {scenario}.log"
        return
    if not args.fixture:
        os.environ["XDG_CONFIG_HOME"] = str(output / "config")
        os.environ["XDG_CACHE_HOME"] = str(output / "cache")
        config = output / "config/docking/dock.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps(
                {
                    "pinned": [
                        {"kind": "applet", "target": "applet://clock"},
                        {"kind": "applet", "target": "applet://calculator"},
                    ],
                    "icon_size": 48,
                    "hide_mode": "none",
                    "show_recent_apps": False,
                    "startup_tips_enabled": False,
                    "update_check_enabled": False,
                }
            ),
            encoding="utf-8",
        )
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    if args.fixture:
        fixture(fullscreen=args.scenario != "transition")
        return
    run_scenario(output, args.scenario)


if __name__ == "__main__":
    main()
