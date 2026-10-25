"""Native MicShield UI, process-session and real synthetic-audio acceptance.

Requires two explicitly named capture endpoints created by the owned lab's
optional microphone overlay. An independent .NET oracle reads/changes native
endpoint mute; separate WASAPI processes measure the actual captured signal.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", action="append", default=[])
    parser.add_argument("--expect-absent", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python in an owned test VM")
    output = args.output.resolve()
    if (output / "result.json").exists():
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
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
        stdin=subprocess.DEVNULL,
        text=True,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=30,
    )
    registries = list(
        (Path(json.loads(cached.stdout)) / "gstreamer-1.0").glob("registry.*.bin")
    )
    if not registries:
        parser.error("Prepare the complete native GStreamer registry first")
    target = output / "cache/gstreamer-1.0"
    target.mkdir(parents=True, exist_ok=True)
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
    from docking.applets.micshield.windows import WindowsMicShieldBackend
    from docking.applets.micshield.windows_audio import CaptureEndpoint
    from tools.windows_desktop_driver import WindowsDesktop
    from tools.windows_micshield_oracle import oracle
    from tools.windows_powerprofiles_smoke import powershell

    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline = native.workarea()
    report = {
        "synthetic_input": True,
        "physical_input": False,
        "states": {},
        "fixtures": {},
    }
    errors, shared = [], {}
    ready = threading.Event()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://micshield"},
                    {"kind": "applet", "target": "applet://clock"},
                ],
                "icon_size": 64,
                "hide_mode": "none",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    def publish():
        temporary = output / "result.tmp"
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(output / "result.json")

    original = app.build_dock_window

    def build(**kwargs):
        ui = original(**kwargs)
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

    initial = oracle()
    identifiers = {row["id"] for row in initial}
    if args.expect_absent:
        assert not initial and not args.device
    else:
        assert len(identifiers) == 2 and set(args.device) == identifiers
        assert any(not row["is_default"] for row in initial)
    report["baseline_oracle"] = initial

    def capture(name):
        ImageGrab.grab(all_screens=True).save(output / (name + ".png"))

    def drive():
        children, settings = [], None
        file_stack = ExitStack()
        try:
            assert ready.wait(90)
            applet = call(lambda: shared["model"].get_applet("applet://micshield"))
            shared["applet"] = applet
            assert isinstance(applet._backend, WindowsMicShieldBackend)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "MicShield dock did not map",
            )

            def point():
                frame = shared["ui"].window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://micshield"
                )
                window = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                return window["rect"][0] + rect.x + rect.w / 2, window["rect"][
                    1
                ] + rect.y + rect.h / 2

            def state():
                return call(lambda: applet._state)

            def idle():
                return call(
                    lambda: (
                        not applet._action_in_progress
                        and not applet._worker._active_keys
                    )
                )

            def menu():
                native.click(*call(point), right=True)
                popup = native.wait_for(
                    lambda: call(lambda: Gtk.grab_get_current()),
                    "MicShield menu did not map",
                )
                rows = call(
                    lambda: [
                        {"label": r.get_label(), "enabled": r.get_sensitive()}
                        for r in popup.get_children()
                        if r.get_label()
                    ]
                )
                return popup, rows

            def choose(popup, label):
                native.key(0x24)
                for _ in range(30):
                    selected = call(lambda: popup.get_selected_item())
                    if (
                        selected
                        and call(lambda selected=selected: selected.get_label())
                        == label
                    ):
                        native.key(0x0D)
                        return
                    native.key(0x28)
                raise AssertionError("Menu selection failed: " + label)

            def snapshot(name):
                report["states"][name] = asdict(state())
                capture(name)
                publish()

            def sample(entry):
                assert entry["process"].poll() is None, "Owned recorder exited"
                try:
                    return json.loads((entry["output"] / "capture.json").read_text())
                except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                    return {}

            def measured(expected, previous=None):
                values = [sample(c) for c in children]
                for entry, value in zip(children, values, strict=True):
                    if value.get("closed") or value.get("samples", 0) <= 1000:
                        return None
                    if (
                        previous
                        and value.get("buffers", 0) <= previous[entry["id"]] + 10
                    ):
                        return None
                    muted = expected[entry["id"]]
                    if not (
                        value.get("rms", 1e9) < 10
                        if muted
                        else value.get("rms", 0) > 1000
                    ):
                        return None
                return values

            def verify_audio(name, expected, previous=None):
                samples = native.wait_for(
                    lambda: measured(expected, previous),
                    "Captured PCM disagrees with mute: " + name,
                    25,
                )
                rows = oracle()
                assert {r["id"]: r["muted"] for r in rows} == expected
                report.setdefault("audio", {})[name] = [
                    {"id": entry["id"], "pid": entry["process"].pid, "sample": value}
                    for entry, value in zip(children, samples, strict=True)
                ]
                report.setdefault("oracle", {})[name] = rows
                publish()

            def settings_page():
                nonlocal settings
                assert not any(w["title"] == "Settings" for w in native.windows())
                popup, rows = menu()
                assert any(
                    r["label"] == "Microphone Settings" and r["enabled"] for r in rows
                )
                choose(popup, "Microphone Settings")
                settings = native.wait_for(
                    lambda: next(
                        (w for w in native.windows() if w["title"] == "Settings"), None
                    ),
                    "Microphone privacy Settings did not open",
                    30,
                )
                time.sleep(2)
                controls = powershell(
                    r"""
Add-Type -AssemblyName UIAutomationClient;Add-Type -AssemblyName UIAutomationTypes;
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]HWND);
$nodes=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
 [System.Windows.Automation.Condition]::TrueCondition);
$rows=@();foreach($node in $nodes){$v=$node.Current;
 $rows+=@{name=$v.Name;id=$v.AutomationId;type=$v.ControlType.ProgrammaticName}}
ConvertTo-Json -InputObject $rows -Depth 3 -Compress
""".replace("HWND", str(settings["hwnd"]))
                )
                assert any(
                    r["name"] == "Microphone" and r["type"] == "ControlType.Text"
                    for r in controls
                )
                assert any("microphone access" in r["name"].lower() for r in controls)
                report["settings_controls"] = controls
                capture("native-microphone-settings")
                native.user.PostMessageW(settings["hwnd"], 0x10, 0, 0)
                native.wait_for(
                    lambda: (
                        not any(w["hwnd"] == settings["hwnd"] for w in native.windows())
                    ),
                    "Owned privacy Settings did not close",
                )
                settings = None

            native.wait_for(idle, "Initial microphone query did not finish", 20)
            if args.expect_absent:
                assert not state().available and not state().error
                popup, rows = menu()
                assert any(r["label"] == "No microphone source found" for r in rows)
                assert any(
                    r["label"] == "Mute Microphone" and not r["enabled"] for r in rows
                )
                snapshot("native-unavailable-menu")
                native.key(0x1B)
                native.click(*call(point))
                assert not state().available
                settings_page()
                report.update(ok=True, native_absence=True)
                return

            assert state().available and state().active is False
            snapshot("native-idle")
            for index, identifier in enumerate(args.device):
                folder = output / f"capture-{index}"
                logs = [
                    file_stack.enter_context(
                        (output / f"capture-{index}.{kind}.log").open("w")
                    )
                    for kind in ("stdout", "stderr")
                ]
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).with_name("windows_micshield_capture.py")),
                        "--output",
                        str(folder),
                        "--device",
                        identifier,
                        "--seconds",
                        "240",
                    ],
                    stdout=logs[0],
                    stderr=logs[1],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                children.append(
                    {"process": process, "id": identifier, "output": folder}
                )
            pids = {c["process"].pid for c in children}
            verify_audio("unmuted", dict.fromkeys(identifiers, False))
            native.wait_for(
                lambda: (
                    idle()
                    and state().active
                    and pids <= {s.pid for s in state().streams}
                ),
                "Owned capture PIDs did not reach UI",
                20,
            )
            assert call(lambda: bool(applet._pulse_timer_id))
            popup, rows = menu()
            assert any(r["label"] == "Microphone active" for r in rows)
            assert all(any(str(pid) in r["label"] for r in rows) for pid in pids)
            report["active_menu"] = rows
            snapshot("native-active-menu")
            native.key(0x1B)
            previous = {c["id"]: sample(c).get("buffers", 0) for c in children}
            native.click(*call(point))
            native.wait_for(
                lambda: idle() and state().muted is True,
                "Native click did not confirm all-input mute",
                20,
            )
            assert state().active
            verify_audio("click-muted", dict.fromkeys(identifiers, True), previous)
            popup, rows = menu()
            snapshot("native-muted-active-menu")
            choose(popup, "Unmute Microphone")
            native.wait_for(
                lambda: idle() and state().muted is False,
                "Native menu did not unmute inputs",
                20,
            )
            verify_audio("menu-unmuted", dict.fromkeys(identifiers, False))

            # Independent .NET writes exercise the normal external-state poll.
            oracle(dict.fromkeys(identifiers, True))
            native.wait_for(
                lambda: idle() and state().muted is True,
                "External all-input mute was not observed",
                15,
            )
            non_default = next(r["id"] for r in initial if not r["is_default"])
            expected = {i: (i != non_default) for i in identifiers}
            oracle(expected)
            native.wait_for(
                lambda: idle() and state().muted is False,
                "Non-default unmuted input was ignored",
                15,
            )
            verify_audio("non-default-unmuted", expected)
            snapshot("native-mixed-inputs")
            settings_page()

            for entry in children:
                (entry["output"] / "stop").write_text("stop")
                assert entry["process"].wait(20) == 0
            native.wait_for(
                lambda: idle() and state().active is False,
                "Capture stop did not reach idle",
                15,
            )
            assert call(lambda: applet._pulse_timer_id) == 0
            snapshot("native-stopped-idle")

            class Fixture:
                def __init__(self):
                    self.endpoints = (
                        CaptureEndpoint("explicit-unknown-fixture", None, (), False),
                    )
                    self.writes = []

                def snapshot(self):
                    return self.endpoints

                def set_muted(self, muted):
                    self.writes.append(muted)
                    self.endpoints = (
                        CaptureEndpoint("explicit-unknown-fixture", muted, (), False),
                    )
                    return True

            fixture = Fixture()

            def install():
                applet._backend.stop()
                applet._poll_generation += 1
                applet._backend = WindowsMicShieldBackend(api=fixture)
                applet._refresh_now()

            call(install)
            native.wait_for(
                lambda: idle() and state().muted is None,
                "Unknown provider fixture did not reach UI",
            )
            popup, rows = menu()
            labels = [r["label"] for r in rows]
            assert "Microphone mute state unknown" in labels
            assert "Microphone activity unknown" in labels
            assert "Microphone idle" not in labels and "Microphone muted" not in labels
            snapshot("fixture-unknown-menu")
            native.key(0x1B)
            native.click(*call(point))
            native.wait_for(
                lambda: idle() and state().muted is True,
                "Unknown-state click did not choose mute",
            )
            assert fixture.writes == [True]
            report["fixtures"] = {
                "unknown_metadata": True,
                "unknown_click_only_mutes": True,
            }
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            capture("failure")
        finally:
            for entry in children:
                process = entry["process"]
                if process.poll() is None:
                    (entry["output"] / "stop").write_text("stop")
                    try:
                        process.wait(20)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        report["forced_capture_cleanup"] = True
            file_stack.close()
            if initial:
                try:
                    restored = oracle({r["id"]: r["muted"] for r in initial})
                    assert {r["id"]: r["muted"] for r in restored} == {
                        r["id"]: r["muted"] for r in initial
                    }
                    report["mute_restored"] = True
                except Exception as exc:
                    errors.append(exc)
                    report.update(ok=False, restore_error=repr(exc))
            if settings is not None:
                native.user.PostMessageW(settings["hwnd"], 0x10, 0, 0)
            if ready.is_set():

                def close():
                    grab = Gtk.grab_get_current()
                    if isinstance(grab, Gtk.MenuShell):
                        grab.cancel()
                    shared["ui"].window.close()

                try:
                    call(close)
                except Exception as exc:
                    errors.append(exc)
                    report.update(ok=False, close_error=repr(exc))
            publish()

    driver = threading.Thread(
        target=drive, name="windows-micshield-smoke", daemon=False
    )
    driver.start()
    try:
        app.main()
        driver.join(90)
        assert not driver.is_alive()
        assert native.workarea() == baseline
        assert shared["applet"]._stopped and shared["applet"]._timer_id == 0
        report["normal_shutdown"] = True
        if errors:
            raise errors[0]
    finally:
        publish()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
