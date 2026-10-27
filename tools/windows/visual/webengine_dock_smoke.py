"""Exercise both Qt web applets through real Windows dock and menu input."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--service", choices=("whatsapp", "telegram"), default="whatsapp"
    )
    parser.add_argument(
        "--notification-point", type=int, nargs=2, required=True, metavar=("X", "Y")
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires the owned Windows desktop")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    profile = tempfile.TemporaryDirectory(prefix="Docking-webengine-dock-smoke-")
    os.environ.update(
        XDG_DATA_HOME=profile.name,
        XDG_CONFIG_HOME=str(output / "config"),
        XDG_CACHE_HOME=str(output / "cache"),
        DOCKING_WEBENGINE_PYTHON=str(args.python),
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    import psutil
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.applets.notifications.windows import NotificationListener
    from docking.applets.webapp.windows_companion.browser import WindowsCompanionBrowser
    from tools.windows.visual.desktop_driver import WindowsDesktop
    from tools.windows.visual.webengine_fixture import TLSFixture

    native = WindowsDesktop()
    assert not any(window["title"] == "Docking" for window in native.windows())
    baseline, explorer = native.workarea(), native.explorer()
    fixture = TLSFixture(output, Path(profile.name))
    fixture.start()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://" + service}
                    for service in ("whatsapp", "telegram", "clock")
                ],
                "icon_size": 64,
                "hide_mode": "none",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        )
    )
    shared, pending, children = {}, {}, {}
    errors, sessions, handles = [], [], []
    ready = threading.Event()
    report = {
        "service": args.service,
        "profile_directory": Path(profile.name).name,
        "stages": [],
    }
    original_build = app.build_dock_window
    original_init, original_packet = (
        WindowsCompanionBrowser.__init__,
        WindowsCompanionBrowser._packet,
    )

    def initialize(browser, **kwargs):
        kwargs["entrypoint"] = Path(__file__).with_name("webengine_fixture.py")
        original_init(browser, **kwargs)

    def packet(browser, values, lifetime):
        if browser._started and browser._lifetime == lifetime:
            event = values.get("event")
            if event == "fixture":
                children[browser.profile.session_name] = values["pid"]
                assert not values["gi_imported"]
            elif event == "probe":
                answer = pending.get(values["id"])
                if answer is not None:
                    answer[1].append(values.get("result"))
                    answer[0].set()
        return original_packet(browser, values, lifetime)

    def build(**kwargs):
        ui = original_build(**kwargs)
        shared.update(ui=ui, model=kwargs["model"])
        ready.set()
        return ui

    WindowsCompanionBrowser.__init__ = initialize
    WindowsCompanionBrowser._packet = packet
    app.build_dock_window = build

    def call(callback):
        done, values, failures = threading.Event(), [], []

        def invoke():
            try:
                values.append(callback())
            except Exception as error:
                failures.append(error)
            finally:
                done.set()
            return False

        GLib.idle_add(invoke)
        assert done.wait(20), "GTK did not respond"
        if failures:
            raise failures[0]
        return values[0]

    sequence = 0

    def script(browser, source):
        nonlocal sequence
        sequence += 1
        identifier, done, values = sequence, threading.Event(), []
        pending[identifier] = (done, values)
        call(
            lambda: browser._process.send(
                {"op": "probe", "id": identifier, "source": source}
            )
        )
        assert done.wait(20), "Companion probe did not reply"
        pending.pop(identifier)
        return values[0]

    def publish(stage=None):
        if stage:
            report["stages"].append(stage)
            print(stage, flush=True)
        (output / "result.json").write_text(json.dumps(report, indent=2))

    def drive():
        dock = None
        try:
            assert ready.wait(90), "Dock did not initialize"
            dock = native.wait_for(
                lambda: next(
                    (
                        window
                        for window in native.windows(os.getpid())
                        if window["title"] == "Docking"
                    ),
                    None,
                ),
                "Dock not visible",
            )
            views = {
                name: call(
                    lambda name=name: shared["model"].get_applet("applet://" + name)
                )
                for name in ("whatsapp", "telegram")
            }
            for view in views.values():
                assert isinstance(view._browser, WindowsCompanionBrowser)
            native.wait_for(
                lambda: call(
                    lambda: all(
                        view._state.phase.value == "ready"
                        and view.item.badge_count == 7
                        and view.item.badge_visible
                        for view in views.values()
                    )
                ),
                "Both applets did not receive their native badges",
                60,
            )
            assert not any(
                call(lambda view=view: view._browser.visible) for view in views.values()
            )
            report["gtk_scale"] = call(lambda: shared["ui"].window.get_scale_factor())
            primary = views[args.service]
            other_name = "telegram" if args.service == "whatsapp" else "whatsapp"
            sibling = views[other_name]
            browser = primary._browser
            publish("actual-dock-both-service-startup-and-exact-badges")
            ImageGrab.grab(all_screens=True).save(output / "dock-both-badges.png")

            def point(name):
                window = shared["ui"].window
                frame = window.geometry.build_frame(main_cursor=-1.0)
                item = next(
                    item.draw_rect
                    for item in frame.item_geometries
                    if item.item.desktop_id == "applet://" + name
                )
                bounds = next(
                    window["rect"]
                    for window in native.windows(os.getpid())
                    if window["hwnd"] == dock["hwnd"]
                )
                scale = window.get_scale_factor()
                return bounds[0] + (item.x + item.w / 2) * scale, bounds[1] + (
                    item.y + item.h / 2
                ) * scale

            def click(name, *, right=False):
                native.user.SetCursorPos(1100, 40)
                time.sleep(0.4)
                position = call(lambda: point(name))
                assert native.window_at(*position) == dock["hwnd"]
                native.click(*position, right=right)

            def foreground(name):
                return any(
                    window["hwnd"] == native.user.GetForegroundWindow()
                    for window in native.windows(children[name])
                )

            def menu(name, label):
                click(name, right=True)
                native.wait_for(
                    lambda: call(lambda: isinstance(Gtk.grab_get_current(), Gtk.Menu)),
                    "Applet menu did not open",
                )
                labels = call(
                    lambda: [
                        item.get_label()
                        for item in Gtk.grab_get_current().get_children()
                        if item.get_visible()
                        and item.get_sensitive()
                        and item.get_label()
                    ]
                )
                report.setdefault("menu_labels", {})[name] = labels
                assert label in labels, labels
                native.key(0x24)
                for _ in range(labels.index(label)):
                    native.key(0x28)
                native.key(0x0D)

            click(args.service)
            native.wait_for(
                lambda: call(lambda: browser.visible) and foreground(args.service),
                "Dock click did not foreground the matching companion",
            )
            window = next(
                window
                for window in native.windows(children[args.service])
                if window["title"] == browser.profile.name
            )
            report["browser_rectangle"], report["workarea"] = (
                window["rect"],
                native.workarea(),
            )
            left, top, right, bottom = window["rect"]
            work = report["workarea"]
            assert (
                left >= work[0]
                and top >= work[1]
                and right <= work[2]
                and bottom <= work[3]
            )
            menu(args.service, primary.hide_label)
            native.wait_for(
                lambda: not call(lambda: browser.visible),
                "Hide menu did not hide companion",
            )
            menu(args.service, primary.open_label)
            native.wait_for(
                lambda: call(lambda: browser.visible) and foreground(args.service),
                "Open menu did not foreground companion",
            )
            publish("real-dock-click-and-open-hide-menus-foreground-correct-service")

            script(browser, "window.fixture.store('primary');true")
            script(sibling._browser, "window.fixture.store('sibling');true")
            for view, value in ((primary, "primary"), (sibling, "sibling")):
                native.wait_for(
                    lambda view=view, value=value: (
                        script(view._browser, "window.fixture.dbValue") == value
                    ),
                    "Isolated applet storage did not settle",
                )
            script(browser, "window.fixture.read();true")
            stored = native.wait_for(
                lambda: script(browser, "window.fixture.storage"),
                "Primary storage missing",
            )
            assert stored["local"] == stored["db"] == "primary"
            publish(
                "simultaneous-applet-profiles-isolate-cookie-localstorage-indexeddb"
            )

            call(browser.hide)
            fixture.modes[args.service] = "login"
            menu(args.service, "Reload")
            native.wait_for(
                lambda: call(lambda: primary._state.phase.value == "login_required"),
                "Reload menu did not expose generated login state",
            )
            fixture.modes[args.service] = "ready"
            menu(args.service, "Reload")
            native.wait_for(
                lambda: call(lambda: primary._state.phase.value == "ready"),
                "Reload menu did not restore ready state",
            )
            publish("real-reload-menu-updates-applet-login-and-ready-state")

            click(other_name)
            native.wait_for(
                lambda: (
                    call(lambda: sibling._browser.visible) and foreground(other_name)
                ),
                "Sibling did not open",
            )
            script(
                browser,
                "document.title="
                + json.dumps(browser.profile.name)
                + ";navigator.clearAppBadge();true",
            )
            native.wait_for(
                lambda: call(
                    lambda: (
                        primary.item.badge_count == 0
                        and primary._state.title == browser.profile.name
                    )
                ),
                "Primary badge did not clear",
            )
            marker = "Docking applet worker " + uuid.uuid4().hex[:8]
            script(
                browser,
                "window.fixture.workerReadyResult=null;Notification.requestPermission().then(()=>window.fixture.startWorker()).then(()=>window.fixture.workerRequest('show',"
                + json.dumps({"title": marker})
                + ")).then(()=>window.fixture.workerReadyResult=true);true",
            )

            def history():
                access, rows = NotificationListener().read(threading.Event())
                assert access == 1
                return [
                    entry.summary
                    for _key, _when, entry in rows
                    if entry.summary == marker
                ]

            native.wait_for(
                history, "Worker notification missing from Windows history", 25
            )
            native.wait_for(
                lambda: call(
                    lambda: primary.item.badge_count == 1 and primary.item.badge_visible
                ),
                "Hidden applet did not project native notification attention",
            )
            assert call(lambda: sibling.item.badge_count) == 7
            ImageGrab.grab(all_screens=True).save(
                output / "dock-notification-attention.png"
            )
            native.key(ord("N"), windows=True)
            time.sleep(0.6)
            ImageGrab.grab(all_screens=True).save(output / "worker-native-card.png")
            native.click(*args.notification_point)
            native.wait_for(
                lambda: call(lambda: browser.visible) and foreground(args.service),
                "Native notification did not foreground its matching applet",
            )
            native.wait_for(
                lambda: any(
                    event["event"] == "clicked"
                    for event in script(browser, "window.fixture.workerEvents")
                ),
                "Native card did not activate the real worker",
            )
            native.wait_for(
                lambda: not history(), "Worker retained its clicked native notification"
            )
            native.wait_for(
                lambda: call(
                    lambda: (
                        primary.item.badge_count == 0 and not primary.item.badge_visible
                    )
                ),
                "Activation did not clear applet attention",
            )
            assert call(lambda: sibling.item.badge_count) == 7
            publish(
                "native-worker-card-activates-matching-applet-and-clears-only-its-attention"
            )

            call(browser.hide)
            menu(args.service, primary.disconnect_label)
            dialog = native.wait_for(
                lambda: call(
                    lambda: next(
                        (
                            window
                            for window in Gtk.Window.list_toplevels()
                            if isinstance(window, Gtk.MessageDialog)
                            and window.get_visible()
                        ),
                        None,
                    )
                ),
                "Disconnect confirmation did not open",
            )
            call(
                lambda: dialog.get_widget_for_response(Gtk.ResponseType.OK).grab_focus()
            )
            old_pid = children[args.service]
            native.key(0x0D)
            native.wait_for(
                lambda: (
                    children[args.service] != old_pid
                    and call(
                        lambda: (
                            browser.visible and primary._state.phase.value == "ready"
                        )
                    )
                ),
                "Confirmed disconnect did not replace and reopen the browser",
                40,
            )
            script(browser, "window.fixture.read();true")
            cleared = native.wait_for(
                lambda: script(browser, "window.fixture.storage"),
                "Cleared storage missing",
            )
            assert cleared == {"local": None, "cookie": "", "db": None}
            script(sibling._browser, "window.fixture.read();true")
            untouched = native.wait_for(
                lambda: script(sibling._browser, "window.fixture.storage"),
                "Sibling storage missing",
            )
            assert (
                untouched["local"] == untouched["db"] == "sibling"
                and "docking_fixture=sibling" in untouched["cookie"]
            )
            assert call(lambda: sibling.item.badge_count) == 7
            publish(
                "real-confirmed-disconnect-reopens-fresh-profile-and-preserves-sibling"
            )
            report["ok"] = True
        except Exception as error:
            errors.append(error)
            report.update(ok=False, error=repr(error))
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            try:
                if "model" in shared:
                    for name in ("whatsapp", "telegram"):
                        view = call(
                            lambda name=name: shared["model"].get_applet(
                                "applet://" + name
                            )
                        )
                        session = call(lambda view=view: view._browser._process)
                        if session is not None:
                            sessions.append(session)
                            if (
                                session._process is not None
                                and session._process.poll() is None
                            ):
                                process = psutil.Process(session._process.pid)
                                handles.extend(
                                    [process, *process.children(recursive=True)]
                                )
                if dock and native.user.IsWindow(dock["hwnd"]):
                    assert native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            except Exception as error:
                errors.append(error)
                report.update(ok=False, cleanup_error=repr(error))
            publish()

    driver = threading.Thread(target=drive, name="webengine-dock-input", daemon=True)
    driver.start()
    try:
        app.main()
        driver.join(45)
        assert not driver.is_alive()
        for session in sessions:
            assert session.closed.wait(30), "Applet companion did not exit"
        native.wait_for(
            lambda: not any(process.is_running() for process in handles),
            "Applet native process tree survived normal dock shutdown",
            20,
        )
        assert native.workarea() == baseline and native.explorer() == explorer
        report["owned_native_pids"] = [process.pid for process in handles]
        report["normal_shutdown"] = True
        if errors:
            raise errors[0]
    except Exception as error:
        report.update(ok=False, error=repr(error))
        raise
    finally:
        app.build_dock_window = original_build
        WindowsCompanionBrowser.__init__ = original_init
        WindowsCompanionBrowser._packet = original_packet
        fixture.close()
        profile._finalizer.detach()
        publish()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
