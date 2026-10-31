"""Exercise classic application identity and shortcuts through the real dock."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires an owned native Windows desktop")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Choose a fresh output directory")
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
    from docking.platform.backends.windows.catalog import read_shell_applications
    from tools.windows.visual.classic_fixture import ClassicApplications
    from tools.windows.visual.desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline, explorer = native.workarea(), native.explorer()
    report, errors, shared = {"baseline_workarea": baseline}, [], {}
    ready = threading.Event()
    original = app.build_dock_window

    def build(**kwargs):
        ui = original(**kwargs)
        shared.update(
            ui=ui,
            model=kwargs["model"],
            backend=kwargs["session_backend"],
            registry=kwargs["application_registry"],
        )
        shared["startup_recent"] = tuple(
            item for item in kwargs["model"].visible_items() if item.is_recent
        )
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

    def capture(name):
        ImageGrab.grab(all_screens=True).save(output / (name + ".png"))

    with ClassicApplications(output) as fixture:

        def catalog():
            rows = {a.name: a for a in read_shell_applications()}
            return (
                {
                    key: rows[fixture.labels[key]]
                    for key in ("First", "Second", "Recent")
                }
                if all(
                    fixture.labels[key] in rows for key in ("First", "Second", "Recent")
                )
                else None
            )

        listed = native.wait_for(catalog, "Owned shortcuts were not discovered", 45)
        applications = {key: listed[key] for key in ("First", "Second")}
        recent_application = listed["Recent"]
        report["catalog"] = {
            key: {
                "id": a.desktop_id,
                "executable": str(a.executable_path),
                "exec": a.exec_line,
            }
            for key, a in listed.items()
        }
        config = output / "config/docking/dock.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            json.dumps(
                {
                    "pinned": [
                        {"kind": "app", "target": a.desktop_id}
                        for a in applications.values()
                    ]
                    + [{"kind": "applet", "target": "applet://clock"}],
                    "icon_size": 64,
                    "hide_mode": "none",
                    "show_recent_apps": True,
                    "recent_apps": [
                        {
                            "desktop_id": recent_application.desktop_id,
                            "last_closed": int(time.time()),
                        }
                    ],
                    "startup_tips_enabled": False,
                    "update_check_enabled": False,
                }
            )
        )

        def drive():
            dock = None
            try:
                assert ready.wait(90)
                dock = native.wait_for(
                    lambda: next(
                        (
                            w
                            for w in native.windows(os.getpid())
                            if w["title"] == "Docking"
                        ),
                        None,
                    ),
                    "Owned dock did not map",
                )
                windows = shared["backend"].windows
                native.wait_for(
                    lambda: call(
                        lambda: all(
                            shared["registry"].get(a.desktop_id) is not None
                            and shared["registry"].get(a.desktop_id).executable_path
                            == a.executable_path
                            for a in listed.values()
                        )
                    ),
                    "Native metadata was not applied to the live registry",
                )

                def recent_state():
                    startup = shared["startup_recent"]
                    assert len(startup) == 1
                    assert startup[0].desktop_id == recent_application.desktop_id
                    current = shared["model"].find_by_desktop_id(
                        recent_application.desktop_id
                    )
                    assert current is startup[0] and current.is_recent
                    assert current.name == recent_application.name
                    return {"id": current.desktop_id, "name": current.name}

                report["restored_recent"] = call(recent_state)
                time.sleep(2)

                def point(key):
                    window = shared["ui"].window
                    frame = window.geometry.build_frame()
                    rect = next(
                        g.draw_rect
                        for g in frame.item_geometries
                        if g.item.desktop_id == applications[key].desktop_id
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

                def click(key):
                    native.user.SetCursorPos(1100, 100)
                    time.sleep(0.3)
                    # Follow the rendered icon while hover zoom changes its
                    # center; a resting-frame coordinate may hit another item.
                    for _ in range(3):
                        target = call(lambda: point(key))
                        native.user.SetCursorPos(int(target[0]), int(target[1]))
                        time.sleep(0.3)

                    def hit():
                        window = shared["ui"].window
                        bounds = next(
                            w["rect"]
                            for w in native.windows(os.getpid())
                            if w["hwnd"] == dock["hwnd"]
                        )
                        scale = window.get_scale_factor()
                        item = window.geometry.build_frame().item_at_point(
                            (int(target[0]) - bounds[0]) / scale,
                            (int(target[1]) - bounds[1]) / scale,
                        )
                        return item.desktop_id if item is not None else None

                    selected = call(hit)
                    report.setdefault("clicks", []).append(
                        {"key": key, "point": target, "selected": selected}
                    )
                    publish()
                    assert selected == applications[key].desktop_id
                    assert native.window_at(*target) == dock["hwnd"]
                    native.click(*target)

                def observed(pid):
                    return call(
                        lambda: [w for w in windows.list_all_windows() if w.pid == pid]
                    )

                def launched(key, phase=0):
                    facts = fixture.facts(key, phase)
                    rows = native.wait_for(
                        lambda: observed(facts["pid"]),
                        "Native window did not reach the tracker",
                    )
                    report.setdefault("launches", []).append(
                        {
                            "key": key,
                            "phase": phase,
                            "facts": facts,
                            "windows": [
                                {
                                    "hwnd": w.id.value,
                                    "pid": w.pid,
                                    "desktop_id": w.desktop_id,
                                    "app_id": w.app_id,
                                    "title": w.title,
                                }
                                for w in rows
                            ],
                        }
                    )
                    publish()
                    capture(f"{key.lower()}-{phase}")
                    assert len(rows) == 1
                    return rows[0]

                def close(row):
                    assert call(lambda: windows.close(row.id)) is ActionResult.OK
                    native.wait_for(
                        lambda: not observed(row.pid),
                        "Closed native window retained its tracker entry",
                    )
                    import psutil

                    with suppress(psutil.NoSuchProcess):
                        assert psutil.Process(row.pid).wait(15) == 0

                rows = {}
                for key in ("First", "Second"):
                    click(key)
                    row = launched(key)
                    assert row.desktop_id == applications[key].desktop_id, report[
                        "launches"
                    ][-1]
                    rows[key] = row
                assert rows["First"].id != rows["Second"].id
                capture("separate-classic-applications")
                for key in ("First", "Second"):
                    # Closing First can reactivate Second. Establish an inactive
                    # target before testing the default activate/minimize toggle.
                    native.click(1100, 100)
                    native.wait_for(
                        lambda key=key: (
                            native.user.GetForegroundWindow() != rows[key].id.value
                            and call(
                                lambda: (
                                    not shared["model"]
                                    .find_by_desktop_id(applications[key].desktop_id)
                                    .is_active
                                )
                            )
                        ),
                        "Owned activation target did not become inactive",
                    )
                    click(key)
                    native.wait_for(
                        lambda key=key: (
                            native.user.GetForegroundWindow() == rows[key].id.value
                        ),
                        "Pin did not activate its matching executable",
                    )
                    report.setdefault("activations", []).append(
                        {"key": key, "foreground": native.user.GetForegroundWindow()}
                    )
                    capture(f"{key.lower()}-activated")
                    close(rows[key])

                fixture.update("First", phase=1)
                click("First")
                updated = launched("First", phase=1)
                assert updated.desktop_id == applications["First"].desktop_id
                expected_cwd = (
                    fixture.executables["First"].parent / "working directory 1 résumé"
                )
                assert Path(report["launches"][-1]["facts"]["cwd"]).samefile(
                    expected_cwd
                )
                close(updated)

                # Another executable with the same filename has no registered
                # shortcut. It must not borrow either installed app's pin.
                child = subprocess.Popen(
                    [str(fixture.executables["Other"]), *fixture.arguments("Other")],
                    cwd=fixture.executables["Other"].parent,
                )
                other = launched("Other")
                assert other.desktop_id not in {a.desktop_id for a in listed.values()}
                report["unlisted_identity"] = other.desktop_id
                close(other)
                assert child.wait(15) == 0
                persisted = json.loads(config.read_text())
                assert [
                    p["target"] for p in persisted["pinned"] if p["kind"] == "app"
                ] == [a.desktop_id for a in applications.values()]
                assert [entry["desktop_id"] for entry in persisted["recent_apps"]] == [
                    recent_application.desktop_id
                ]
                report.update(ok=True, pin_persistence=True)
            except Exception as exc:
                errors.append(exc)
                report["error"] = repr(exc)
                report["foreground_on_failure"] = native.user.GetForegroundWindow()
                capture("failure")
                publish()
            finally:
                if dock is not None:
                    native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
                else:
                    GLib.idle_add(app.quit)

        driver = threading.Thread(target=drive, daemon=True)
        driver.start()
        try:
            app.main()
        finally:
            driver.join(30)
            report["normal_shutdown"] = not driver.is_alive()
            publish()
            app.build_dock_window = original
        if errors:
            raise errors[0]
    assert native.workarea() == baseline
    assert native.explorer() == explorer
    assert not any(w["title"] == "Docking" for w in native.windows(os.getpid()))
    report["fixture_cleanup"] = True
    publish()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
