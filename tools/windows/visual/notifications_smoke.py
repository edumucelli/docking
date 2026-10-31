"""Exercise native Notifications through the dock and independent Settings UI."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shutdown-read", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires the owned Windows desktop")
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
    from docking.applets.notifications.windows import (
        NotificationListener,
        QuietHours,
        WindowsNotificationsBackend,
    )
    from tools.windows.visual.desktop_driver import WindowsDesktop

    native, quiet = WindowsDesktop(), QuietHours()
    assert not any(w["title"] == "Docking" for w in native.windows())
    assert not any(w["title"] == "Settings" for w in native.windows())
    baseline, explorer, original_profile = (
        native.workarea(),
        native.explorer(),
        quiet.selected_profile(),
    )
    identity = "Docking.Notification.Fixture." + uuid.uuid4().hex
    ready, closed, arm, held = (threading.Event() for _ in range(4))
    shared, errors = {}, []
    report = {"pid": os.getpid(), "fixture_id": identity, "stages": [], "io": []}
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://notifications"},
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
    original_build, originals = app.build_dock_window, {}

    def instrument(name, original):
        def invoke(backend, *values, **kwargs):
            record = {"method": name, "thread": threading.get_ident()}
            report["io"].append(record)
            try:
                result = original(backend, *values, **kwargs)
                if isinstance(result, bool):
                    record["accepted"] = result
                if name == "poll" and arm.is_set():
                    held.set()
                    assert backend._cancelled.wait(20), "Stop did not cancel held read"
                return result
            finally:
                if name == "close":
                    closed.set()

        return invoke

    for name in ("poll", "set_paused", "close"):
        original = getattr(WindowsNotificationsBackend, name)
        originals[name] = original
        setattr(WindowsNotificationsBackend, name, instrument(name, original))

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
        assert done.wait(20), "GTK did not respond"
        if failures:
            raise failures[0]
        return values[0]

    def fixture(action, tag="first", title="Docking generated notification A"):
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("notifications_fixture.py")),
                action,
                "--id",
                identity,
                "--tag",
                tag,
                "--title",
                title,
                "--body",
                "Generated native notification — café",
            ],
            check=True,
            capture_output=True,
            timeout=40,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def settings_state():
        os.startfile("ms-settings:notifications")
        window = native.wait_for(
            lambda: next(
                (w for w in native.windows() if w["title"] == "Settings"), None
            ),
            "Settings missing",
            30,
        )
        try:
            time.sleep(1.5)
            script = r"""
Add-Type -AssemblyName UIAutomationClient;Add-Type -AssemblyName UIAutomationTypes;
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]HWND);
$id='SystemSettings_Notifications_QuietHours_MuteNotification_Enabled_ToggleSwitch';
$condition=[System.Windows.Automation.PropertyCondition]::new(
 [System.Windows.Automation.AutomationElement]::AutomationIdProperty,$id);
$node=$null;
for($i=0;$i -lt 30 -and $null -eq $node;$i++) {
 $node=$root.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$condition);
 if($null -eq $node){Start-Sleep -Milliseconds 500};
}
if($null -eq $node){throw 'Do not disturb toggle missing'};
$pattern=$node.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern);
@{name=$node.Current.Name;id=$node.Current.AutomationId;
state=$pattern.Current.ToggleState.ToString()}|ConvertTo-Json -Compress
""".replace("HWND", str(window["hwnd"]))
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    base64.b64encode(script.encode("utf-16-le")).decode(),
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return json.loads(result.stdout)
        finally:
            native.user.PostMessageW(window["hwnd"], 0x10, 0, 0)
            native.wait_for(
                lambda: not native.user.IsWindow(window["hwnd"]),
                "Owned Settings did not close",
            )

    def publish(stage=None):
        if stage:
            report["stages"].append(stage)
        (output / "result.json").write_text(json.dumps(report, indent=2))

    def drive():
        dock = view = None
        try:
            assert ready.wait(90)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock missing",
            )
            view = call(lambda: shared["model"].get_applet("applet://notifications"))
            native.wait_for(
                lambda: call(lambda: view._state.available and not view._loading),
                "Notifications unavailable",
            )
            assert "docking.applets.notifications.linux" not in sys.modules
            assert quiet.set_profile("Microsoft.QuietHoursProfile.Unrestricted")
            call(view._tick)
            native.wait_for(
                lambda: call(lambda: not view._state.paused and not view._pending),
                "Initial DND state unavailable",
            )
            report["gtk_scale"] = call(lambda: shared["ui"].window.get_scale_factor())

            def point():
                window = shared["ui"].window
                frame = window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://notifications"
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
                native.user.SetCursorPos(1100, 40)
                time.sleep(0.4)
                position = call(point)
                assert native.window_at(*position) == dock["hwnd"]
                native.click(*position, right=right)

            click()
            native.wait_for(
                lambda: call(lambda: view._state.paused and not view._pending),
                "Dock click did not pause notifications",
            )
            report["settings_on"] = settings_state()
            assert report["settings_on"]["state"] == "On"
            ImageGrab.grab(all_screens=True).save(output / "notifications-paused.png")
            publish("native-click-pauses-confirmed-by-settings")
            click(True)
            native.wait_for(
                lambda: call(lambda: isinstance(Gtk.grab_get_current(), Gtk.Menu)),
                "Menu missing",
            )
            # Home selects the first enabled item: Do Not Disturb.
            native.key(0x24)
            native.key(0x0D)
            native.wait_for(
                lambda: call(lambda: not view._state.paused and not view._pending),
                "Menu did not resume notifications",
            )
            report["settings_off"] = settings_state()
            assert report["settings_off"]["state"] == "Off"
            publish("native-menu-resumes-confirmed-by-settings")
            fixture("send")
            native.wait_for(
                lambda: call(
                    lambda: any(
                        e.summary == "Docking generated notification A"
                        for e in view._history
                    )
                ),
                "First native notification missing",
                30,
            )
            fixture("send", "second", "Docking generated notification B")
            native.wait_for(
                lambda: call(
                    lambda: any(
                        e.summary == "Docking generated notification B"
                        for e in view._history
                    )
                ),
                "Second native notification missing",
                30,
            )
            assert (
                call(
                    lambda: next(
                        e.body
                        for e in view._history
                        if e.summary == "Docking generated notification B"
                    )
                )
                == "Generated native notification — café"
            )
            native.user.SetCursorPos(*map(int, call(point)))
            before = call(lambda: view._history_index)
            native.scroll(120)
            native.wait_for(
                lambda: call(lambda: view._history_index != before),
                "Wheel did not browse notification history",
            )
            assert call(lambda: view._history_badge_count()) >= 2
            ImageGrab.grab(all_screens=True).save(output / "notifications-history.png")
            publish("native-toasts-history-badge-and-wheel")
            click(True)
            native.wait_for(
                lambda: call(lambda: isinstance(Gtk.grab_get_current(), Gtk.Menu)),
                "Menu missing",
            )
            native.key(0x24)
            native.key(0x28)
            native.key(0x0D)
            native.wait_for(
                lambda: call(lambda: not view._history), "Clear History menu failed"
            )
            fixture("send", "third", "Docking generated notification C")
            native.wait_for(
                lambda: call(
                    lambda: (
                        len(view._history) == 1
                        and view._history[0].summary
                        == "Docking generated notification C"
                    )
                ),
                "Cleared history reappeared or new notification missing",
                30,
            )
            publish("clear-local-history-retains-new-events-only")
            listener = NotificationListener()
            access, rows = listener.read(threading.Event())
            assert access == 1
            owned = tuple(
                row
                for row in rows
                if row[2].summary == "Docking generated notification C"
            )
            retained = {
                (key, created)
                for key, created, entry in rows
                if entry.summary != "Docking generated notification C"
            }
            assert len(owned) == 1 and listener.remove(owned, threading.Event())
            after = listener.read(threading.Event())
            assert (
                after[0] == 1
                and {(key, created) for key, created, _entry in after[1]} == retained
            )
            native.wait_for(
                lambda: call(lambda: view._state.pending == len(after[1])),
                "Native removal was not reflected in pending count",
            )
            publish("native-targeted-remove-retains-other-notifications")
            if args.shutdown_read:
                arm.set()
                call(view._tick)
                assert held.wait(20), "Read did not reach held barrier"
            assert native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            native.wait_for(
                lambda: not native.user.IsWindow(dock["hwnd"]), "Dock did not close"
            )
            assert closed.wait(20), "Notification IO worker did not close"
            publish(
                "normal-close-during-native-read"
                if args.shutdown_read
                else "normal-close-releases-worker"
            )
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            fixture("remove")
            assert quiet.set_profile(original_profile)
            native.wait_for(
                lambda: quiet.selected_profile() == original_profile,
                "Original notification profile did not return",
            )
            report["profile_restored"] = True
            report["owned_notifications_removed"] = not any(
                entry.summary
                in (
                    "Docking generated notification A",
                    "Docking generated notification B",
                    "Docking generated notification C",
                )
                for _key, _time, entry in NotificationListener().read(
                    threading.Event()
                )[1]
            )
            if dock and native.user.IsWindow(dock["hwnd"]):
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            publish()

    driver = threading.Thread(
        target=drive, name="notifications-native-smoke", daemon=True
    )
    driver.start()
    try:
        app.main()
        driver.join(60)
        assert not driver.is_alive()
        assert report.get("profile_restored")
        assert native.workarea() == baseline and native.explorer() == explorer
        assert len({record["thread"] for record in report["io"]}) == 1
        assert report["io"][0]["thread"] != threading.get_ident()
        assert all(record.get("accepted", True) for record in report["io"])
        assert report["owned_notifications_removed"]
        report["normal_shutdown"] = True
        if errors:
            raise errors[0]
    except Exception as exc:
        report.update(ok=False, error=repr(exc))
        raise
    finally:
        app.build_dock_window = original_build
        for name, original in originals.items():
            setattr(WindowsNotificationsBackend, name, original)
        publish()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
