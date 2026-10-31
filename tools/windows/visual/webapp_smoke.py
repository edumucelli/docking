"""Exercise the Windows web applets with an owned native page fixture."""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--service", choices=("whatsapp", "telegram"), default="whatsapp"
    )
    parser.add_argument(
        "--notifications",
        "--popup-activation",
        dest="notifications",
        action="store_true",
    )
    parser.add_argument(
        "--notification-point",
        nargs=2,
        type=int,
        metavar=("X", "Y"),
        help="Native card point inspected visually when UI Automation misses it",
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires the owned Windows desktop")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from docking.platform.backends.windows.webview import loader_path

    os.environ["DOCKING_WEBVIEW2_LOADER"] = str(loader_path())
    profile = tempfile.TemporaryDirectory(prefix="Docking-webapp-smoke-")
    os.environ["XDG_DATA_HOME"] = profile.name
    os.environ["XDG_CONFIG_HOME"] = str(output / "config")
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.applets.notifications.windows import NotificationListener
    from docking.applets.telegram.browser import PROFILE as TELEGRAM
    from docking.applets.webapp.windows import WindowsWebAppBrowser
    from docking.applets.whatsapp.browser import PROFILE as WHATSAPP
    from docking.platform.backends.windows.ffi import bind
    from docking.platform.backends.windows.toasts import ToastPublisher
    from tools.windows.visual.desktop_driver import WindowsDesktop
    from tools.windows.visual.webapp_fixture import WebAppPageFixture

    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline, explorer = native.workarea(), native.explorer()
    ready = threading.Event()
    service = WHATSAPP if args.service == "whatsapp" else TELEGRAM
    fixture = WebAppPageFixture(service)
    other_service = TELEGRAM if service is WHATSAPP else WHATSAPP
    fixtures = {
        service.session_name: fixture,
        other_service.session_name: WebAppPageFixture(other_service),
    }
    shared, errors = {}, []
    report = {
        "pid": os.getpid(),
        "service": service.session_name,
        "stages": [],
        "profile_directory": Path(profile.name).name,
        "toast_io": [],
    }
    ui_thread = threading.get_ident()
    toast_originals = {}
    publishers = set()

    def owned_history(publisher):
        from contextlib import ExitStack

        from docking.platform.backends.windows.com import (
            GUID,
            object_result,
            query,
            value,
        )

        if publisher._runtime is None:
            return []
        with ExitStack() as scope:
            history = query(
                scope,
                publisher._history,
                GUID.parse("3bc3d253-2f31-4092-9129-8ad5abf067da"),
            )
            with publisher._runtime.string(publisher.identity) as identity:
                rows = object_result(scope, history, 7, ctypes.c_void_p, identity)
            result = []
            for index in range(value(rows, 7, ctypes.c_uint32).value):
                row = object_result(scope, rows, 6, ctypes.c_uint32, index)
                properties = query(
                    scope, row, GUID.parse("9dfb9fd1-143a-490e-90bf-b9fba7132de7")
                )
                result.append(
                    {
                        "tag": publisher._runtime.text(properties, 7),
                        "group": publisher._runtime.text(properties, 9),
                    }
                )
            return result

    for method_name in ("show", "hide", "close"):
        original = getattr(ToastPublisher, method_name)
        toast_originals[method_name] = original

        def invoke(publisher, *values, _name=method_name, _original=original):
            publishers.add(publisher)
            record = {
                "method": _name,
                "thread": threading.get_ident(),
                "identity": publisher.identity,
                "publisher": id(publisher),
            }
            report["toast_io"].append(record)
            try:
                result = _original(publisher, *values)
                record["native_complete"] = True
                record["owned_history"] = owned_history(publisher)
                return result
            except Exception as error:
                record["error"] = type(error).__name__
                raise
            finally:
                record["complete"] = True

        setattr(ToastPublisher, method_name, invoke)
    notification_title = (
        "Docking generated web-app notification " + uuid.uuid4().hex[:8]
    )
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://" + service.session_name},
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
    original_build, original_ready = (
        app.build_dock_window,
        WindowsWebAppBrowser._browser_ready,
    )

    def browser_ready(browser):
        fixtures[browser.profile.session_name].install(browser._engine)
        original_ready(browser)

    WindowsWebAppBrowser._browser_ready = browser_ready

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

    def publish(stage=None):
        if stage:
            report["stages"].append(stage)
        (output / "result.json").write_text(json.dumps(report, indent=2))

    def drive():
        dock = view = sibling = None
        try:
            assert ready.wait(90)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock missing",
            )
            view = call(
                lambda: shared["model"].get_applet("applet://" + service.session_name)
            )
            browser = view._browser
            assert isinstance(browser, WindowsWebAppBrowser)
            native.wait_for(
                lambda: call(
                    lambda: (
                        view._state.phase.value == "ready"
                        and view._state.unread_count == 7
                    )
                ),
                "Native page or badge missing",
                40,
            )
            assert "docking.applets.webapp.linux" not in sys.modules
            assert "docking.applets.webapp.linux_notifications" not in sys.modules
            assert not call(lambda: browser.visible)
            report["gtk_scale"] = call(lambda: shared["ui"].window.get_scale_factor())
            publish("hidden-start-native-ready-and-exact-badge")

            def script(source, target=browser):
                done, results = threading.Event(), []

                def result(text):
                    results.append(json.loads(text))
                    done.set()

                call(lambda: target._engine.execute(source, result))
                assert done.wait(20), "JavaScript callback did not arrive"
                return results[0]

            def point():
                window = shared["ui"].window
                frame = window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://" + service.session_name
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
                lambda: call(lambda: browser.visible), "Dock click did not show browser"
            )
            browser_window = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows(os.getpid())
                        if w["title"] == service.name
                    ),
                    None,
                ),
                "Browser window missing",
            )
            report["browser_rectangle"] = browser_window["rect"]
            report["browser_workarea"] = native.workarea()
            rect, work = browser_window["rect"], report["browser_workarea"]
            assert rect[0] >= work[0] and rect[1] >= work[1]
            assert rect[2] <= work[2] and rect[3] <= work[3]
            box = script(
                "(()=>{const r=document.getElementById('input')"
                ".getBoundingClientRect();"
                "return {x:r.x+r.width/2,y:r.y+r.height/2,ratio:devicePixelRatio}})()"
            )

            class Point(ctypes.Structure):
                _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]

            origin = Point()
            parent = call(lambda: browser._engine._parent)
            assert bind(
                native.user,
                "ClientToScreen",
                ctypes.c_int32,
                ctypes.c_void_p,
                ctypes.POINTER(Point),
            )(parent, ctypes.byref(origin))
            native.click(
                origin.x + box["x"] * box["ratio"], origin.y + box["y"] * box["ratio"]
            )
            native.type_text("café Windows")
            native.wait_for(
                lambda: (
                    script("document.getElementById('input').value") == "café Windows"
                ),
                "Native text input was not reflected by the browser process",
            )
            time.sleep(0.3)  # Let Chromium composite the accepted Unicode input.
            ImageGrab.grab(all_screens=True).save(output / "webapp-native-browser.png")
            native.key(0x1B)
            native.wait_for(
                lambda: not call(lambda: browser.visible), "Escape did not hide browser"
            )
            click()
            native.wait_for(
                lambda: call(lambda: browser.visible), "Browser did not reopen"
            )
            assert script("document.getElementById('input').value") == "café Windows"
            publish("native-input-unicode-escape-and-retained-window")
            assert script("window.fixture.store('kept')")
            native.wait_for(
                lambda: script("window.fixture.dbValue") == "kept",
                "IndexedDB write missing",
            )
            sibling = call(
                lambda: WindowsWebAppBrowser(
                    profile=other_service,
                    on_phase=lambda *_args: None,
                    on_title=lambda *_args: None,
                    on_badge=lambda *_args: None,
                    on_notification=lambda: None,
                    on_attention_cleared=lambda: None,
                )
            )
            call(sibling.start)
            native.wait_for(
                lambda: call(lambda: sibling.phase.value == "ready"),
                "Second profile did not start independently",
                40,
            )
            assert not call(lambda: sibling.visible)
            assert script("window.fixture.read()", sibling)
            separate = native.wait_for(
                lambda: script("window.fixture.storage", sibling),
                "Second profile storage read failed",
            )
            assert separate["local"] is None and separate["db"] is None
            assert "docking_fixture=" not in separate["cookie"]
            assert script("window.fixture.store('isolated')", sibling)
            native.wait_for(
                lambda: script("window.fixture.dbValue", sibling) == "isolated",
                "Second profile storage write failed",
            )
            assert script("window.fixture.read()")
            native.wait_for(
                lambda: (
                    script("window.fixture.storage")
                    and script("window.fixture.storage.db") == "kept"
                ),
                "Second profile modified the first profile's session",
            )
            call(sibling.stop)
            sibling = None
            publish("simultaneous-service-profiles-isolate-persistent-storage")
            call(browser.stop)
            click()
            native.wait_for(
                lambda: call(
                    lambda: view._state.phase.value == "ready" and browser._ready
                ),
                "Persisted browser restart failed",
                40,
            )
            assert script("window.fixture.read()")
            stored = native.wait_for(
                lambda: script("window.fixture.storage"),
                "Persisted storage read failed",
            )
            report["owned_storage_after_restart"] = stored
            assert (
                stored["local"] == stored["db"] == "kept"
                and "docking_fixture=kept" in stored["cookie"]
            )
            publish("native-controller-restart-preserves-cookie-localstorage-indexeddb")

            call(lambda: setattr(fixture, "mode", "login"))
            call(browser.reload)
            native.wait_for(
                lambda: call(lambda: view._state.phase.value == "login_required"),
                "Generated login landmark was not detected",
            )
            call(lambda: setattr(fixture, "mode", "ready"))
            call(browser.reload)
            native.wait_for(
                lambda: call(lambda: view._state.phase.value == "ready"),
                "Ready state did not return",
            )
            # Invalid page traffic must not restart or corrupt the browser.
            script("window.chrome.webview.postMessage('x'.repeat(5000)); true")
            time.sleep(0.3)
            assert call(lambda: view._state.phase.value == "ready")
            publish("native-login-ready-and-bounded-message-policy")

            if args.notifications:
                call(browser.hide)
                script(
                    "document.title="
                    + json.dumps(service.name)
                    + "; navigator.clearAppBadge(); true"
                )
                script(
                    "window.fixture.notify("
                    + json.dumps(notification_title)
                    + "); true"
                )
                native.wait_for(
                    lambda: script("window.fixture.sent===true"),
                    "Native notification permission/constructor failed",
                )
                native.wait_for(
                    lambda: call(lambda: view._state.notification_count > 0),
                    "Native notification activity was not counted",
                )
                listener = NotificationListener()

                def owned_notifications():
                    access, rows = listener.read(threading.Event())
                    assert access == 1
                    return tuple(
                        row for row in rows if row[2].summary == notification_title
                    )

                native.wait_for(
                    owned_notifications,
                    "Native browser notification never reached Windows history",
                    25,
                )
                report["native_notification_delivered"] = True
                # History activation remains available after the brief popup.
                native.key(ord("N"), windows=True)
                time.sleep(0.5)
                script_text = r"""
Add-Type -AssemblyName UIAutomationClient;Add-Type -AssemblyName UIAutomationTypes;
$root=[System.Windows.Automation.AutomationElement]::RootElement;
$condition=[System.Windows.Automation.PropertyCondition]::new(
 [System.Windows.Automation.AutomationElement]::NameProperty,'TITLE');
$rows=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition);
$answer=$null;foreach($node in $rows){$v=$node.Current;
 if(-not $v.IsOffscreen -and $v.BoundingRectangle.Width -gt 0) {
  $r=$v.BoundingRectangle;$answer=@{x=$r.X+$r.Width/2;y=$r.Y+$r.Height/2};break;
 }}
ConvertTo-Json -InputObject $answer -Compress
""".replace("TITLE", notification_title)

                def toast_point():
                    result = subprocess.run(
                        [
                            "powershell.exe",
                            "-NoProfile",
                            "-NonInteractive",
                            "-EncodedCommand",
                            base64.b64encode(script_text.encode("utf-16-le")).decode(),
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=30,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    return json.loads(result.stdout) if result.stdout.strip() else None

                if args.notification_point is not None:
                    x, y = args.notification_point
                    ImageGrab.grab(bbox=(x - 160, y - 55, x + 160, y + 60)).save(
                        output / "owned-native-toast-card.png"
                    )
                    position = {"x": x, "y": y}
                    report["notification_target"] = "visually-verified-native-card"
                else:
                    position = native.wait_for(
                        toast_point,
                        "Owned Windows history notification was not visible",
                        20,
                    )
                native.click(position["x"], position["y"])
                native.wait_for(
                    lambda: script("window.fixture.clicked===true"),
                    "Native notification did not dispatch DOM click",
                )
                native.wait_for(
                    lambda: call(lambda: browser.visible),
                    "Native notification click did not reopen browser",
                )
                native.wait_for(
                    lambda: any(
                        w["title"] == service.name
                        and w["hwnd"] == native.user.GetForegroundWindow()
                        for w in native.windows(os.getpid())
                    ),
                    "Native notification did not focus the service window",
                )
                publish("native-notification-history-dom-click-and-window-activation")
                first = notification_title + " replacement-first"
                second = notification_title + " replacement-next"
                script("window.fixture.notify(" + json.dumps(first) + "); true")
                native.wait_for(
                    lambda: any(
                        row[2].summary == first
                        for row in listener.read(threading.Event())[1]
                    ),
                    "First replacement toast absent from history",
                )
                script("window.fixture.notify(" + json.dumps(second) + "); true")

                def family():
                    access, rows = listener.read(threading.Event())
                    assert access == 1
                    return tuple(
                        row
                        for row in rows
                        if row[2].summary.startswith(notification_title)
                    )

                native.wait_for(
                    lambda: tuple(row[2].summary for row in family()) == (second,),
                    "Same-tag replacement retained the wrong history entry",
                )
                native.wait_for(
                    lambda: script("window.fixture.closedTitles").count(first) == 1,
                    "Replacement did not close the old DOM notification once",
                )
                from docking.platform.backends.windows.com import GUID
                from docking.platform.backends.windows.com_callback import ComObject

                handler_iid = bytes(GUID.parse("47c32d23-1e94-4733-85f1-d9bf4acd0974"))
                report["before_web_close"] = {
                    "close_handler_references": call(
                        lambda: [
                            cb._references
                            for cb in ComObject._live.values()
                            if cb._iid == handler_iid
                        ]
                    ),
                    "closed_titles": script("window.fixture.closedTitles"),
                    "shown_titles": script("window.fixture.shownTitles"),
                    "native_tokens": call(
                        lambda: list(browser._notifier._notifications)
                    ),
                }
                report["web_close_result"] = script(
                    "(()=>{const n=window.fixture.notification;try{n.close();"
                    "return {ok:true,title:n.title,tag:n.tag}}catch(e){"
                    "return {ok:false,name:e.name,message:e.message}}})()"
                )
                assert report["web_close_result"]["ok"], report["web_close_result"]
                native.wait_for(lambda: not family(), "Web close retained history")
                native.wait_for(
                    lambda: script("window.fixture.closedTitles").count(second) == 1,
                    "Web close duplicated or missed the DOM close event",
                )
                publish("native-tag-replacement-and-web-close-remove-owned-history")

                def close_handlers():
                    return call(
                        lambda: sum(
                            cb._iid == handler_iid for cb in ComObject._live.values()
                        )
                    )

                report["replacement_cycles"] = []
                for cycle in range(3):
                    titles = []
                    for replacement in range(12):
                        title = f"{notification_title} cycle-{cycle}-{replacement}"
                        titles.append(title)
                        script("window.fixture.notify(" + json.dumps(title) + ");true")
                        native.wait_for(
                            lambda title=title: (
                                title in script("window.fixture.shownTitles")
                            ),
                            "Repeated replacement missed DOM show",
                        )
                    native.wait_for(
                        lambda title=titles[-1]: (
                            tuple(row[2].summary for row in family()) == (title,)
                        ),
                        "Repeated replacement retained stale Windows history",
                    )
                    assert close_handlers() == len(titles)
                    script("window.fixture.notification.close();true")
                    native.wait_for(
                        lambda: not family(), "Repeated close retained history"
                    )
                    native.wait_for(
                        lambda: close_handlers() == 0,
                        "Replacement group retained native callback references",
                    )
                    closed = script("window.fixture.closedTitles")
                    assert all(closed.count(title) == 1 for title in titles)
                    report["replacement_cycles"].append(
                        {"replacements": len(titles), "remaining_close_handlers": 0}
                    )
                publish("repeated-tag-lifetimes-release-all-native-callbacks")

            call(browser.hide)
            click(True)
            native.wait_for(
                lambda: call(lambda: isinstance(Gtk.grab_get_current(), Gtk.Menu)),
                "Web-app menu missing",
            )
            native.key(0x24)
            for _ in range(3):
                native.key(0x28)
            native.key(0x0D)
            dialog = native.wait_for(
                lambda: call(
                    lambda: next(
                        (
                            w
                            for w in Gtk.Window.list_toplevels()
                            if isinstance(w, Gtk.MessageDialog) and w.get_visible()
                        ),
                        None,
                    )
                ),
                "Disconnect confirmation missing",
            )
            call(
                lambda: dialog.get_widget_for_response(Gtk.ResponseType.OK).grab_focus()
            )
            native.key(0x0D)
            native.wait_for(
                lambda: call(
                    lambda: view._state.phase.value == "ready" and not browser._clearing
                ),
                "Native disconnect did not reload",
                40,
            )
            assert script("window.fixture.read()")
            cleared = native.wait_for(
                lambda: script("window.fixture.storage"), "Cleared storage read failed"
            )
            assert (
                cleared["local"] is None
                and cleared["db"] is None
                and "docking_fixture=" not in cleared["cookie"]
            )
            publish("native-confirmed-disconnect-clears-cookie-localstorage-indexeddb")
            if args.notifications:
                script(
                    "window.fixture.notify("
                    + json.dumps(notification_title + " shutdown")
                    + "); true"
                )
                native.wait_for(family, "Owned shutdown toast absent from history")
            assert native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            native.wait_for(
                lambda: not native.user.IsWindow(dock["hwnd"]), "Dock did not close"
            )
            publish("normal-close-releases-browser")
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            if args.notifications and report.get("native_notification_delivered"):
                access, rows = NotificationListener().read(threading.Event())
                report["retained_owned_titles"] = [
                    [row[0], row[2].app_name, row[2].summary]
                    for row in rows
                    if row[2].summary.startswith(notification_title)
                ]
                report["retained_owned_sources"] = []
                with NotificationListener()._listener() as (
                    runtime,
                    scope,
                    native_listener,
                ):
                    from docking.platform.backends.windows.com import object_result

                    for identifier, _created, entry in rows:
                        if entry.summary.startswith(notification_title):
                            current = object_result(
                                scope,
                                native_listener,
                                11,
                                ctypes.c_uint32,
                                int(identifier),
                            )
                            if not current.value:
                                report["retained_owned_sources"].append(None)
                                continue
                            info = object_result(scope, current, 7)
                            report["retained_owned_sources"].append(
                                runtime.text(info, 7)
                            )
                probe = (
                    "import json,threading;"
                    "from docking.applets.notifications.windows "
                    "import NotificationListener;"
                    "access,rows=NotificationListener().read(threading.Event());"
                    "print(json.dumps([access,[[row[0],row[2].app_name,row[2].summary] "
                    "for row in rows if row[2].summary.startswith("
                    + repr(notification_title)
                    + ")]]))"
                )
                fresh = subprocess.run(
                    [sys.executable, "-c", probe],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                report["fresh_reader_owned_titles"] = json.loads(fresh.stdout)
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            if sibling is not None:
                call(sibling.stop)
            if args.notifications:
                listener = NotificationListener()
                if report.get("ok"):
                    native.wait_for(
                        lambda: (
                            not any(
                                row[2].summary.startswith(notification_title)
                                for row in listener.read(threading.Event())[1]
                            )
                        ),
                        "Normal close retained owned history",
                    )
                access, rows = listener.read(threading.Event())
                report["cleanup_history_access"] = access
                owned = tuple(
                    row for row in rows if row[2].summary.startswith(notification_title)
                )
                report["owned_history_after_normal_close"] = len(owned)
                if report.get("ok"):
                    assert not owned, "Normal close retained owned history"
                if owned:
                    assert listener.remove(owned, threading.Event())
            if dock and native.user.IsWindow(dock["hwnd"]):
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            publish()

    def run_driver():
        try:
            drive()
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            publish()

    driver = threading.Thread(
        target=run_driver, name="webapp-native-smoke", daemon=True
    )
    driver.start()
    try:
        app.main()
        driver.join(45)
        assert not driver.is_alive()
        assert native.workarea() == baseline and native.explorer() == explorer
        assert not any(
            w["pid"] == os.getpid() and w["title"] == service.name
            for w in native.windows()
        )
        if args.notifications:
            published = {
                row["publisher"]
                for row in report["toast_io"]
                if row["method"] == "show"
            }
            native.wait_for(
                lambda: published.issubset(
                    {
                        row["publisher"]
                        for row in report["toast_io"]
                        if row["method"] == "close" and row.get("complete")
                    }
                ),
                "Native toast finalizer did not finish",
            )
            assert all(row.get("complete") for row in report["toast_io"])
            assert not any("error" in row for row in report["toast_io"])
            assert all(row["thread"] != ui_thread for row in report["toast_io"])
            for publisher in published:
                assert (
                    len(
                        {
                            row["thread"]
                            for row in report["toast_io"]
                            if row["publisher"] == publisher
                        }
                    )
                    == 1
                )
        report["normal_shutdown"] = True
        if errors:
            raise errors[0]
    except Exception as exc:
        report.update(ok=False, error=repr(exc))
        raise
    finally:
        app.build_dock_window = original_build
        WindowsWebAppBrowser._browser_ready = original_ready
        for method_name, original in toast_originals.items():
            setattr(ToastPublisher, method_name, original)
        # Cleanup belongs to the subsequent process audit, after Chromium has
        # released its asynchronous profile handles.
        profile._finalizer.detach()
        publish()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
