"""Verify actual Caffeine power requests, native controls and owned cleanup.

A labeled countdown fixture shortens remaining time to three seconds while
retaining the real GTK timer and Windows power APIs. The shutdown mode adds a
controlled barrier after a successful native display request to exercise stop
while acquisition is in flight; it never substitutes a successful API result.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stop-during-acquire", action="store_true")
    parser.add_argument("--fail-first-set", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires an owned interactive Windows desktop")
    if args.stop_during_acquire and args.fail_first_set:
        parser.error("Choose only one failure or shutdown fixture")
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
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.applets.caffeine.windows import WindowsInhibitor, WindowsPowerRequests
    from tools.windows.visual.desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline, explorer = native.workarea(), native.explorer()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://caffeine"},
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
    ready, blocked, finish = threading.Event(), threading.Event(), threading.Event()
    shared, errors = {}, []
    report = {
        "pid": os.getpid(),
        "stages": [],
        "native_calls": [],
        "stop_during_acquire": args.stop_during_acquire,
        "fail_first_set": args.fail_first_set,
    }
    reason = "Caffeine: keeping awake"
    original_build = app.build_dock_window
    original_create, original_set = (
        WindowsPowerRequests.create,
        WindowsPowerRequests.set,
    )
    original_clear, original_close = (
        WindowsPowerRequests.clear,
        WindowsPowerRequests.close,
    )

    def record(operation, handle, kind=None):
        report["native_calls"].append(
            {
                "operation": operation,
                "handle": handle,
                "kind": kind,
                "thread": threading.get_ident(),
            }
        )

    def create(api, reason):
        value = original_create(api, reason)
        record("create", value)
        return value

    def set_request(api, handle, kind):
        if args.fail_first_set and "native_failure_fixture" not in report:
            # Exercise a genuine Windows error on our owned request object.
            # Production always uses the valid Display/System enum values.
            try:
                original_set(api, handle, 0x7FFFFFFF)
            except OSError as exc:
                report["native_failure_fixture"] = {
                    "input": "deliberately invalid native request kind",
                    "winerror": exc.winerror,
                }
                raise
            raise AssertionError("Windows accepted an invalid request kind")
        original_set(api, handle, kind)
        record("set", handle, kind)
        if args.stop_during_acquire and kind == 0:
            blocked.set()
            if not finish.wait(30):
                raise OSError("Controlled acceptance barrier timed out")

    def clear(api, handle, kind):
        original_clear(api, handle, kind)
        record("clear", handle, kind)

    def close(api, handle):
        original_close(api, handle)
        record("close", handle)

    WindowsPowerRequests.create, WindowsPowerRequests.set = create, set_request
    WindowsPowerRequests.clear, WindowsPowerRequests.close = clear, close

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

    def requests():
        text = subprocess.run(
            [
                str(Path(os.environ["SYSTEMROOT"]) / "System32/powercfg.exe"),
                "/requests",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
        tokens = re.split(r"(?m)^([A-Z]+):\s*$", text)
        sections = {tokens[i]: tokens[i + 1] for i in range(1, len(tokens) - 1, 2)}
        assert "DISPLAY" in sections and "SYSTEM" in sections, (
            "Requires an English lab oracle"
        )
        return {key: reason in sections[key] for key in ("DISPLAY", "SYSTEM")}

    def expect_requests(active):
        expected = {"DISPLAY": active, "SYSTEM": active}
        native.wait_for(
            lambda: requests() == expected,
            "Independent native power requests disagree",
            20,
        )
        return expected

    def drive():
        dock = None
        try:
            assert ready.wait(90)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock did not map",
            )
            applet = call(lambda: shared["model"].get_applet("applet://caffeine"))
            shared["applet"] = applet
            assert isinstance(applet._inhibitor, WindowsInhibitor)
            assert "docking.applets.caffeine.linux" not in sys.modules
            assert applet._inhibitor._api is None and not report["native_calls"]
            expect_requests(False)
            assert not call(lambda: applet._data.active)
            stage("lazy-inactive-startup")

            def point():
                window = shared["ui"].window
                frame = window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://caffeine"
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
            if args.fail_first_set:
                native.wait_for(
                    lambda: call(lambda: applet._error and not applet._request_pending),
                    "Failed native request did not reach the UI",
                )
                assert not call(lambda: applet._data.active)
                expect_requests(False)
                assert report["native_failure_fixture"]["winerror"] > 0
                click(right=True)
                menu = native.wait_for(
                    lambda: call(
                        lambda: (
                            current
                            if isinstance(current := Gtk.grab_get_current(), Gtk.Menu)
                            else None
                        )
                    ),
                    "Failure menu did not open",
                )
                assert "Unavailable" in call(
                    lambda: [row.get_label() for row in menu.get_children()]
                )
                native.user.SetCursorPos(1100, 50)
                time.sleep(0.6)
                ImageGrab.grab(all_screens=True).save(output / "native-error.png")
                native.key(0x1B)
                stage("controlled-native-error-unavailable-no-lease")
                click()
            if args.stop_during_acquire:
                assert blocked.wait(10)
                assert requests() == {"DISPLAY": True, "SYSTEM": False}
                assert not call(lambda: applet._data.active)
                assert call(lambda: applet._request_pending)
                # GTK remains responsive while the real native worker is held.
                ImageGrab.grab(all_screens=True).save(output / "pending.png")
                assert native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
                native.wait_for(
                    lambda: not native.user.IsWindow(dock["hwnd"]),
                    "Dock did not close during acquisition",
                )
                finish.set()
                expect_requests(False)
                stage("native-acquire-stop-without-gtk-delivery")
            else:
                expect_requests(True)
                native.wait_for(
                    lambda: call(
                        lambda: applet._data.active and not applet._request_pending
                    ),
                    "UI did not confirm native inhibition",
                )
                stage("native-click-acquires-display-system")
                click(right=True)
                menu = native.wait_for(
                    lambda: call(
                        lambda: (
                            current
                            if isinstance(current := Gtk.grab_get_current(), Gtk.Menu)
                            else None
                        )
                    ),
                    "Caffeine menu did not open",
                )
                report["menu_labels"] = call(
                    lambda: [
                        w.get_label() for w in menu.get_children() if w.get_label()
                    ]
                )
                assert (
                    "Active" in report["menu_labels"]
                    and "30 min" in report["menu_labels"]
                )
                native.user.SetCursorPos(1100, 50)
                time.sleep(0.6)
                ImageGrab.grab(all_screens=True).save(output / "caffeine-menu.png")
                native.key(0x24)
                native.key(0x28)
                native.key(0x28)
                native.key(0x0D)
                native.wait_for(
                    lambda: call(lambda: applet._data.duration_min == 30),
                    "Native duration selection did not apply",
                )
                assert json.loads(config.read_text())["applet_prefs"]["caffeine"] == {
                    "duration_min": 30
                }
                assert len(
                    [v for v in report["native_calls"] if v["operation"] == "create"]
                ) == 1 + int(args.fail_first_set)
                assert (
                    len([v for v in report["native_calls"] if v["operation"] == "set"])
                    == 2
                )
                report["countdown_fixture"] = (
                    "Remaining time shortened to 3 seconds; "
                    "actual GTK timer and power APIs unchanged"
                )
                call(
                    lambda: setattr(applet, "_data", replace(applet._data, remaining=3))
                )
                expect_requests(False)
                native.wait_for(
                    lambda: call(
                        lambda: (
                            not applet._data.active
                            and not applet._request_pending
                            and applet._timer_id == 0
                        )
                    ),
                    "Countdown did not confirm release",
                )
                stage("native-menu-persistence-real-countdown")
                click()
                expect_requests(True)
                native.wait_for(
                    lambda: call(lambda: applet._data.active),
                    "Second acquisition missing",
                )
                click()
                expect_requests(False)
                native.wait_for(
                    lambda: call(lambda: not applet._data.active),
                    "Click did not release",
                )
                stage("native-click-releases")
                call(
                    lambda: (
                        applet.on_clicked(),
                        applet.on_clicked(),
                        applet.on_clicked(),
                    )
                )
                expect_requests(True)
                native.wait_for(
                    lambda: call(
                        lambda: applet._data.active and not applet._request_pending
                    ),
                    "Latest rapid intent did not settle",
                )
                stage("explicit-rapid-requests-latest-intent")
                ImageGrab.grab(all_screens=True).save(output / "caffeine-active.png")
                assert native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
                native.wait_for(
                    lambda: not native.user.IsWindow(dock["hwnd"]),
                    "Active dock did not close",
                )
                expect_requests(False)
                stage("normal-close-releases-active-lease")
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            finish.set()
            if dock is not None and native.user.IsWindow(dock["hwnd"]):
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            publish()

    driver = threading.Thread(target=drive, name="windows-caffeine-smoke", daemon=False)
    driver.start()
    try:
        app.main()
        driver.join(45)
        assert not driver.is_alive()
        applet = shared["applet"]
        native.wait_for(
            lambda: not applet._session.pending, "Cleanup worker did not finish"
        )
        expect_requests(False)
        assert not applet._inhibitor.active and applet._inhibitor._handle is None
        assert native.workarea() == baseline and native.explorer() == explorer
        creates = [v for v in report["native_calls"] if v["operation"] == "create"]
        closes = [v for v in report["native_calls"] if v["operation"] == "close"]
        assert len(creates) == len(closes) and creates
        assert all(v["thread"] != threading.get_ident() for v in report["native_calls"])
        report.update(
            normal_shutdown=True, workarea_after=baseline, final_requests=requests()
        )
        if errors:
            raise errors[0]
    finally:
        app.build_dock_window = original_build
        WindowsPowerRequests.create, WindowsPowerRequests.set = (
            original_create,
            original_set,
        )
        WindowsPowerRequests.clear, WindowsPowerRequests.close = (
            original_clear,
            original_close,
        )
        finish.set()
        publish()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
