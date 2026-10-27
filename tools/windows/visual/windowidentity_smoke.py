"""Verify real hosted app grouping, native actions and preview identity changes."""

from __future__ import annotations

import argparse
import hashlib
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
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.backends.base import ActionResult
    from tools.windows.visual.desktop_driver import WindowsDesktop, read_shared_json

    registry = ApplicationRegistry()
    registry.refresh()
    apps = {
        label: next(
            info
            for info in registry.snapshot()
            if info.desktop_id.casefold() == application_id.casefold()
        )
        for label, application_id in {
            "calculator": "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
            "clock": "Microsoft.WindowsAlarms_8wekyb3d8bbwe!App",
        }.items()
    }
    native = WindowsDesktop()
    assert not any(
        w["title"] in {"Docking", "Calculator", "Clock"} for w in native.windows()
    )
    baseline, explorer = native.workarea(), native.explorer()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "app", "target": info.desktop_id} for info in apps.values()
                ]
                + [{"kind": "applet", "target": "applet://clock"}],
                "icon_size": 64,
                "hide_mode": "none",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        )
    )
    ready, shared, errors = threading.Event(), {}, []
    report = {"pid": os.getpid(), "applications": {}, "actions": []}
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
        opened = {}
        try:
            assert ready.wait(90)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock did not map",
            )
            windows, previews = shared["backend"].windows, shared["backend"].previews
            # Mapping precedes the dock's first positioning and input-region
            # updates. Give that initial layout the same settling time as the
            # independent hover-preview desktop check.
            time.sleep(2)

            def rows(label):
                return call(lambda: tuple(windows.list_windows(apps[label].desktop_id)))

            def icon(label):
                window = shared["ui"].window
                frame = window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == apps[label].desktop_id
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

            def click(label):
                point = call(lambda: icon(label))
                assert native.window_at(*point) == dock["hwnd"], point
                report["actions"].append({"click": label, "point": point})
                publish()
                native.click(*point)

            for label in apps:
                click(label)
                row = native.wait_for(
                    lambda name=label: rows(name)[0] if len(rows(name)) == 1 else None,
                    "Real packaged application did not match its pinned item",
                    45,
                )
                opened[label] = row
                report["applications"][label] = {
                    "id": row.id.value,
                    "owner_pid": row.pid,
                    "app_id": row.app_id,
                    "desktop_id": row.desktop_id,
                    "title": row.title,
                    "class": row.wm_class,
                }
                assert row.app_id == apps[label].desktop_id
                publish()
            assert opened["calculator"].id != opened["clock"].id
            assert opened["calculator"].pid == opened["clock"].pid
            report["same_host_different_applications"] = True

            # The app pins must act on distinct frames although their native PID
            # is shared. Exercise the regular click-to-activate/minimize path.
            click("calculator")
            native.wait_for(
                lambda: (
                    native.user.GetForegroundWindow() == opened["calculator"].id.value
                ),
                "Pinned Calculator did not activate its own frame",
            )
            native.wait_for(
                lambda: rows("calculator")[0].active, "Active tracking did not settle"
            )
            click("calculator")
            native.wait_for(
                lambda: native.user.IsIconic(opened["calculator"].id.value),
                "Calculator did not minimize",
            )
            assert not native.user.IsIconic(opened["clock"].id.value)
            click("calculator")
            native.wait_for(
                lambda: not native.user.IsIconic(opened["calculator"].id.value),
                "Calculator did not restore",
            )
            report["actions"].extend(
                ["calculator-activated", "calculator-minimized", "calculator-restored"]
            )

            def preview(window_id, name):
                image = native.wait_for(
                    lambda: call(
                        lambda: previews.capture(window_id, width=320, height=200)
                    ),
                    "Native GPU preview did not arrive",
                    20,
                )
                image.image.savev(str(output / (name + ".png")), "png", [], [])
                return hashlib.sha256(bytes(image.image.get_pixels())).hexdigest()

            hashes = {
                label: preview(row.id, label + "-preview")
                for label, row in opened.items()
            }
            assert hashes["calculator"] != hashes["clock"]
            report["preview_hashes"] = hashes
            native.user.SetCursorPos(1100, 100)
            ImageGrab.grab(all_screens=True).save(output / "hosted-applications.png")
            publish()

            fixture_dir.mkdir()
            fixture = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("windowpicker_fixture.py")),
                    "--output",
                    str(fixture_dir),
                    "--app-ids",
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=(fixture_dir / "stderr.log").open("w"),
            )

            def fixture_state():
                try:
                    return read_shared_json(fixture_dir / "state.json")
                except (FileNotFoundError, json.JSONDecodeError):
                    return None

            state = native.wait_for(
                fixture_state, "Explicit identity fixture did not map", 30
            )
            target_id = state["windows"]["Target"]
            sibling_id = state["windows"]["Sibling"]

            def target():
                return call(
                    lambda: next(
                        (
                            w
                            for w in windows.list_all_windows()
                            if w.id.value == target_id
                        ),
                        None,
                    )
                )

            old = native.wait_for(
                lambda: (
                    target()
                    if target() is not None
                    and target().app_id == "Docking.Fixture.Target"
                    else None
                ),
                "Fixture app ID missing",
            )
            sibling = call(
                lambda: next(
                    w for w in windows.list_all_windows() if w.id.value == sibling_id
                )
            )
            assert (
                old.pid == sibling.pid == fixture.pid and old.app_id != sibling.app_id
            )
            first = preview(old.id, "identity-before")
            command = fixture_dir / "identity-0001.request.json"
            temporary = command.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {
                        "window": "Target",
                        "app_id": "Docking.Fixture.Changed",
                        "label": "Changed application view",
                    }
                )
            )
            temporary.rename(command)
            native.wait_for(
                lambda: (
                    fixture_state()["app_ids"].get(str(target_id))
                    == "Docking.Fixture.Changed"
                ),
                "Fixture did not change native identity",
            )

            def reassigned():
                windows.refresh()
                row = next(
                    w for w in windows.list_all_windows() if w.id.value == target_id
                )
                assert row.pid == old.pid and row.app_id == "Docking.Fixture.Changed"
                assert (
                    windows._native.perform(old, "minimize") is ActionResult.NOT_FOUND
                )
                assert previews.capture(old.id, width=320, height=200) is None
                return row

            current = call(reassigned)
            assert not native.user.IsIconic(target_id)
            second = preview(current.id, "identity-after")
            assert first != second
            report["reassigned_frame"] = {
                "hwnd": target_id,
                "pid": old.pid,
                "before": old.app_id,
                "after": current.app_id,
                "old_action": "not_found",
                "old_pixels_discarded": True,
                "before_hash": first,
                "after_hash": second,
            }
            for hwnd in state["windows"].values():
                native.user.PostMessageW(hwnd, 0x10, 0, 0)
            assert fixture.wait(15) == 0
            report["fixture_exit"] = 0

            for label, row in opened.items():
                assert (
                    call(lambda value=row.id: windows.close(value)) is ActionResult.OK
                )
                native.wait_for(
                    lambda name=label: not rows(name),
                    "Closed app retained its running entry",
                )
                report["actions"].append(label + "-closed")
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
            report.update(
                ok=False,
                error=repr(exc),
                native_windows=native.windows(),
                snapshots=[
                    {
                        "id": row.id.value,
                        "pid": row.pid,
                        "app_id": row.app_id,
                        "desktop_id": row.desktop_id,
                        "title": row.title,
                    }
                    for row in call(lambda: windows.list_all_windows())
                ]
                if dock is not None
                else [],
            )
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            for row in opened.values():
                native.user.PostMessageW(row.id.value, 0x10, 0, 0)
            if fixture is not None and fixture.poll() is None:
                for window in native.windows(fixture.pid):
                    native.user.PostMessageW(window["hwnd"], 0x10, 0, 0)
                try:
                    fixture.wait(10)
                except subprocess.TimeoutExpired:
                    fixture.kill()
                    fixture.wait(10)
                    report["forced_fixture_cleanup"] = True
            if dock is not None:
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            publish()

    driver = threading.Thread(target=drive, name="windows-identity-smoke", daemon=False)
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
