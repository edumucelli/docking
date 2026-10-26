"""Verify Windows Show Desktop through the complete dock and real windows.

The separate fixture process owns a normal window, a non-minimizable window,
an already-minimized window and a non-minimizable always-on-top window. Test reservation
and Window Dodge without closing or modifying unrelated application windows.
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


def fixture():
    from gi.repository import GLib, Gtk

    windows = []

    def closed(window):
        windows.remove(window)
        if not windows:
            Gtk.main_quit()

    for i, name in enumerate(("Normal", "Fixed", "Minimized", "Topmost")):
        window = Gtk.Window(title="Docking desktop fixture " + name)
        window.set_default_size(360, 240)
        window.move(20 + i * 190, 40 + i * 140)
        if name == "Fixed":
            window.set_decorated(False)
            window.set_resizable(False)
            window.set_default_size(600, 260)
            window.move(340, 490)
        if name == "Topmost":
            window.set_decorated(False)
            window.set_resizable(False)
            window.set_keep_above(True)
            window.set_default_size(220, 100)
            window.move(20, 460)
        window.add(Gtk.Label(label="Owned " + name + " window"))
        window.connect("destroy", closed)
        windows.append(window)
        window.show_all()
        if name == "Minimized":
            GLib.timeout_add(200, lambda w=window: (w.iconify(), False)[1])
    Gtk.main()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--hide-mode", choices=("none", "window-dodge"), default="none")
    parser.add_argument("--restart-explorer", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires an owned interactive Windows desktop")
    output = args.output.resolve()
    if not args.fixture:
        if output.exists() and any(output.iterdir()):
            parser.error("Use a fresh output directory")
        output.mkdir(parents=True, exist_ok=True)
        os.environ["XDG_CONFIG_HOME"] = str(output / "config")
        os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib

    if args.fixture:
        fixture()
        return
    from PIL import ImageGrab

    import docking.app as app
    from docking.platform.backends.base import ActionResult
    from docking.platform.backends.windows.desktop import WindowsDesktopActionService
    from docking.platform.backends.windows.ffi import bind
    from tools.windows.desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    iconic = bind(native.user, "IsIconic", ctypes.c_int32, ctypes.c_void_p)
    style = bind(
        native.user,
        "GetWindowLongPtrW",
        ctypes.c_ssize_t,
        ctypes.c_void_p,
        ctypes.c_int32,
    )
    baseline = native.workarea()
    shared, report, errors = (
        {},
        {"pid": os.getpid(), "states": {}, "hide_mode": args.hide_mode},
        [],
    )
    ready = threading.Event()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://desktop"},
                    {"kind": "applet", "target": "applet://clock"},
                    {"kind": "applet", "target": "applet://systemtray"},
                ],
                "icon_size": 64,
                "hide_mode": args.hide_mode,
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        )
    )
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
        assert done.wait(20), "GTK did not service an observation"
        if failures:
            raise failures[0]
        return values[0]

    def publish():
        (output / "result.json").write_text(json.dumps(report, indent=2))

    def capture(path):
        previous = None
        stable_since = time.monotonic()

        def settled():
            nonlocal previous, stable_since
            frame = ImageGrab.grab(all_screens=True)
            # IsIconic changes before DWM's minimize animation ends. Compare
            # the fixture area, excluding the dock clock and taskbar updates.
            pixels = frame.crop((0, 0, frame.width, frame.height * 3 // 4)).tobytes()
            if pixels != previous:
                previous, stable_since = pixels, time.monotonic()
            return frame if time.monotonic() - stable_since >= 0.3 else None

        native.wait_for(settled, "Fixture pixels did not settle", 8).save(path)

    def drive():
        dock = child = service = None
        owned = {}
        try:
            assert ready.wait(90)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock did not map",
            )
            service = shared["backend"].desktop_actions
            assert isinstance(service, WindowsDesktopActionService)
            assert shared["backend"].capabilities.supports_show_desktop
            applet = call(lambda: shared["model"].get_applet("applet://desktop"))
            assert call(lambda: applet._desktop_actions is service)
            clicked = threading.Event()
            original_click = applet.on_clicked

            def observed_click():
                original_click()
                clicked.set()

            call(lambda: setattr(applet, "on_clicked", observed_click))
            child = subprocess.Popen(
                [sys.executable, __file__, "--output", str(output), "--fixture"],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            native.wait_for(
                lambda: len(native.windows(child.pid)) == 4,
                "Owned fixture windows did not map",
                30,
            )
            owned = {
                w["title"].rsplit(" ", 1)[-1]: w for w in native.windows(child.pid)
            }
            native.wait_for(
                lambda: iconic(owned["Minimized"]["hwnd"]),
                "Pre-minimized fixture did not minimize",
            )
            assert not style(owned["Fixed"]["hwnd"], -16) & 0x20000
            report["fixtures"] = owned
            native.click(80, 80)
            native.wait_for(
                lambda: native.user.GetForegroundWindow() == owned["Normal"]["hwnd"],
                "Normal fixture did not receive foreground",
            )

            def rows():
                return call(
                    lambda: {
                        w.title.rsplit(" ", 1)[-1]: {
                            "visible": w.visible,
                            "minimized": w.minimized,
                            "id": w.id.value,
                        }
                        for w in shared["backend"].windows.list_all_windows()
                        if w.pid == child.pid
                    }
                )

            native.wait_for(
                lambda: len(rows()) == 4, "Native window tracking incomplete"
            )
            initial_ids = {v["id"] for v in rows().values()}

            def icon():
                window = shared["ui"].window
                frame = window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://desktop"
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

            def click_applet():
                clicked.clear()
                point = call(icon)
                if native.window_at(*point) != dock["hwnd"]:
                    native.user.SetCursorPos(int(point[0]), 751)
                    native.wait_for(
                        lambda: native.window_at(*call(icon)) == dock["hwnd"],
                        "Edge did not reveal the desktop applet",
                    )
                native.click(*call(icon))
                assert clicked.wait(5), "Native click did not reach the Desktop applet"

            def settle(shown, label):
                native.wait_for(
                    lambda: not service.pending, "Desktop worker did not finish", 15
                )
                assert service.last_result is ActionResult.OK, service.last_result
                native.wait_for(
                    lambda: bool(iconic(owned["Normal"]["hwnd"])) == shown,
                    "Native normal window state did not match desktop action",
                )
                assert iconic(owned["Minimized"]["hwnd"])
                assert not iconic(owned["Fixed"]["hwnd"])
                assert not iconic(owned["Topmost"]["hwnd"])
                fixed = owned["Fixed"]["rect"]
                point = (
                    fixed[0] + (fixed[2] - fixed[0]) * 3 / 4,
                    fixed[1] + (fixed[3] - fixed[1]) / 3,
                )
                native.user.SetCursorPos(1100, 100)
                native.wait_for(
                    lambda: (
                        (native.window_at(*point) != owned["Fixed"]["hwnd"]) == shown
                    ),
                    "Non-minimizable window was not covered/restored",
                )
                native.wait_for(
                    lambda: rows().get("Fixed", {}).get("visible") == (not shown),
                    "Covered window still reported overlap visibility",
                )
                assert {v["id"] for v in rows().values()} == initial_ids
                if args.hide_mode == "window-dodge":
                    native.wait_for(
                        lambda: (
                            call(lambda: shared["ui"].window.autohide.hide_offset)
                            == (0.0 if shown else 1.0)
                        ),
                        "Window Dodge animation did not reach its endpoint",
                        8,
                    )
                    native.wait_for(
                        lambda: (
                            (native.window_at(*call(icon)) == dock["hwnd"]) == shown
                        ),
                        "Window Dodge did not follow desktop coverage",
                        15,
                    )
                else:
                    assert native.window_at(*call(icon)) == dock["hwnd"]
                report["states"][label] = {
                    "shown": shown,
                    "rows": rows(),
                    "hide_offset": call(
                        lambda: shared["ui"].window.autohide.hide_offset
                    ),
                    "foreground": int(native.user.GetForegroundWindow() or 0),
                }
                capture(output / (label + ".png"))
                publish()

            click_applet()
            settle(True, "desktop-shown")
            click_applet()
            settle(False, "windows-restored")
            for wanted in (True, True, False, False):
                assert call(lambda v=wanted: service.show_desktop(v)) is ActionResult.OK
                settle(wanted, f"explicit-{wanted}-{len(report['states'])}")
            results = call(
                lambda: [
                    service.show_desktop(v).value for v in (True, True, False, False)
                ]
            )
            assert results == ["ok"] * 4
            settle(False, "rapid-idempotent")
            native.key(0x44, windows=True)
            native.wait_for(
                lambda: iconic(owned["Normal"]["hwnd"]), "External Win+D did not act"
            )
            assert call(lambda: service.show_desktop(True)) is ActionResult.OK
            settle(True, "external-win-d")
            native.key(0x44, windows=True)
            native.wait_for(
                lambda: not iconic(owned["Normal"]["hwnd"]),
                "External restore did not act",
            )
            assert call(lambda: service.show_desktop(False)) is ActionResult.OK
            settle(False, "external-restored")
            call(service.stop)
            assert call(service.show_desktop) is ActionResult.NOT_FOUND
            assert not service.pending
            call(service.start)
            assert call(lambda: service.show_desktop(True)) is ActionResult.OK
            settle(True, "service-restarted")
            assert call(lambda: service.show_desktop(False)) is ActionResult.OK
            settle(False, "final-restored")
            if args.restart_explorer:
                report["explorer_restart"] = native.restart_explorer()
                # A visible taskbar precedes usable shell commands. Even the
                # native Win+D shortcut can be ignored during this interval.
                # Establish readiness with the independent Windows control,
                # then restore the owned fixture before testing our backend.
                ignored = 0
                for _ in range(10):
                    native.key(0x44, windows=True)
                    try:
                        native.wait_for(
                            lambda: iconic(owned["Normal"]["hwnd"]),
                            "Explorer's own Show Desktop is not ready",
                            2,
                        )
                    except AssertionError:
                        ignored += 1
                    else:
                        break
                else:
                    raise AssertionError(
                        "Explorer's native Show Desktop never recovered"
                    )
                report["shell_readiness"] = {"ignored_native_win_d": ignored}
                native.key(0x44, windows=True)
                native.wait_for(
                    lambda: not iconic(owned["Normal"]["hwnd"]),
                    "Native readiness probe did not restore the owned fixture",
                )
                assert call(lambda: service.show_desktop(True)) is ActionResult.OK
                settle(True, "after-explorer-restart")
                assert call(lambda: service.show_desktop(False)) is ActionResult.OK
                settle(False, "restart-windows-restored")
                click_applet()
                settle(True, "restart-applet-shown")
                click_applet()
                settle(False, "restart-applet-restored")
            assert not any(
                n.startswith("docking.platform.backends.x11") for n in sys.modules
            )
            report.update(
                ok=True, no_x11_imports=True, all_running_windows_retained=True
            )
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            if service is not None:
                try:
                    call(service.start)
                    call(lambda: service.show_desktop(False))
                    native.wait_for(
                        lambda: not service.pending,
                        "Cleanup desktop request did not finish",
                        8,
                    )
                except Exception as exc:
                    errors.append(exc)
                    report.update(ok=False, restore_error=repr(exc))
            for value in owned.values():
                native.user.PostMessageW(value["hwnd"], 0x10, 0, 0)
            if child is not None:
                try:
                    report["fixture_exit"] = child.wait(20)
                    assert child.returncode == 0
                    if report.get("ok") and service is not None:
                        report["empty_desktop_results"] = []
                        for wanted in (True, True, False, False):
                            assert (
                                call(lambda v=wanted: service.show_desktop(v))
                                is ActionResult.OK
                            )
                            native.wait_for(
                                lambda: not service.pending,
                                "Empty desktop request did not finish",
                                15,
                            )
                            assert service.last_result is ActionResult.OK
                            report["empty_desktop_results"].append(
                                service.last_result.value
                            )
                except Exception as exc:
                    errors.append(exc)
                    report.update(ok=False, fixture_error=repr(exc))
            if dock is not None:
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            elif ready.is_set():
                call(lambda: shared["ui"].window.close())
            publish()

    driver = threading.Thread(target=drive, name="windows-desktop-smoke", daemon=False)
    driver.start()
    try:
        app.main()
        driver.join(30)
        assert not driver.is_alive()
        assert (
            shared["backend"].desktop_actions.show_desktop() is ActionResult.NOT_FOUND
        )
        assert not shared["backend"].desktop_actions.pending
        assert shared["backend"].desktop_actions._thread is None
        report["worker_stopped"] = True
        report["workarea_after"] = native.workarea()
        assert native.workarea() == baseline
        report["normal_shutdown"] = True
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
