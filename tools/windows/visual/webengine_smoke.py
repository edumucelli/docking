"""Exercise the Qt browser backend, native worker toasts and owned process cleanup."""

from __future__ import annotations


def main():
    import argparse
    import json
    import os
    import sys
    import tempfile
    import threading
    import time
    import uuid
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--service", choices=("whatsapp", "telegram"), default="whatsapp"
    )
    parser.add_argument(
        "--notification-point", type=int, nargs=2, required=True, metavar=("X", "Y")
    )
    parser.add_argument("--hang-at-end", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires the owned Windows desktop")
    root = args.output.resolve()
    if root.exists() and any(root.iterdir()):
        parser.error("Use a fresh output directory")
    root.mkdir(parents=True, exist_ok=True)
    folder = tempfile.TemporaryDirectory(prefix="Docking-webengine-smoke-")
    os.environ["XDG_DATA_HOME"] = folder.name
    os.environ["XDG_CACHE_HOME"] = str(root / "cache")
    os.environ["DOCKING_WEBENGINE_PYTHON"] = str(args.python)
    os.environ["DOCKING_BROWSER_FIXTURE_OUTPUT"] = str(root)
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    import psutil
    from docking.applets.telegram.browser import TelegramBrowser
    from docking.applets.whatsapp.browser import WhatsAppBrowser
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    from docking.applets.notifications.implementations.windows.backend import (
        NotificationListener,
    )
    from docking.applets.webapp.implementations.windows.companion.browser import (
        WindowsCompanionBrowser,
    )
    from tools.windows.visual.desktop_driver import WindowsDesktop
    from tools.windows.visual.webengine_fixture import TLSFixture

    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline, explorer = native.workarea(), native.explorer()

    fixture = TLSFixture(root, Path(folder.name))
    fixture.start()
    report = {
        "service": args.service,
        "stages": [],
        "phases": [],
        "badges": [],
        "child_pids": [],
        "profile_directory": Path(folder.name).name,
        "gtk_pulses": 0,
    }
    errors = []
    pending = {}
    counter = 0
    original_packet = WindowsCompanionBrowser._packet

    def packet(browser, values, lifetime):
        if values.get("event") == "probe":
            answer = pending.get(values["id"])
            if answer:
                answer[1].append(values.get("result"))
                answer[0].set()
        elif values.get("event") == "fixture":
            report["child_pids"].append(values["pid"])
            report["child_gi_imported"] = values["gi_imported"]
        elif values.get("event") == "fixture_origin":
            report["view_origin"] = values
        elif values.get("event") == "fixture_hang":
            report["fixture_hung"] = True

        return original_packet(browser, values, lifetime)

    WindowsCompanionBrowser._packet = packet
    browser_class = WhatsAppBrowser if args.service == "whatsapp" else TelegramBrowser
    browser = browser_class(
        entrypoint=Path(__file__).with_name("webengine_fixture.py"),
        on_phase=lambda phase, error: report["phases"].append((phase.value, error)),
        on_title=lambda title: None,
        on_badge=lambda count, visible: report["badges"].append((count, visible)),
        on_notification=lambda: report.update(
            notifications=report.get("notifications", 0) + 1
        ),
        on_attention_cleared=lambda: None,
    )

    def pulse():
        report["gtk_pulses"] += 1
        return True

    pulse_source = GLib.timeout_add(50, pulse)

    def call(fn):
        done, results, failures = threading.Event(), [], []

        def run():
            try:
                results.append(fn())
            except Exception as error:
                failures.append(error)
            finally:
                done.set()
            return False

        GLib.idle_add(run)
        assert done.wait(20), "GTK call timed out"
        if failures:
            raise failures[0]
        return results[0]

    def script(source):
        nonlocal counter
        counter += 1
        identifier = counter
        done, results = threading.Event(), []
        pending[identifier] = (done, results)
        call(
            lambda: browser._process.send(
                {"op": "probe", "id": identifier, "source": source}
            )
        )
        assert done.wait(20), "Companion script timed out"
        pending.pop(identifier, None)
        return results[0]

    def request(action, title=None):
        script(
            "window.fixture.workerReply=null;window.fixture.workerRequest("
            + json.dumps(action)
            + ","
            + json.dumps({"title": title})
            + ").then(rows=>window.fixture.workerReply={ok:true,rows})"
            ".catch(e=>window.fixture.workerReply={ok:false,error:e.message});true"
        )
        answer = native.wait_for(
            lambda: script("window.fixture.workerReply"), "No worker reply", 25
        )
        assert answer["ok"], answer
        return answer["rows"]

    def stage(name):
        report["stages"].append(name)
        (root / "result.json").write_text(json.dumps(report, indent=2))
        print(name, flush=True)

    def drive():
        session = None
        marker = "Docking companion worker " + uuid.uuid4().hex[:8]
        try:
            native.wait_for(
                lambda: call(lambda: browser.phase.value == "ready"),
                "Companion not ready",
                45,
            )
            assert report["badges"][-1] == (7, True), report
            assert not browser.visible and not report["child_gi_imported"]
            stage("shared-BrowserBackend-hidden-ready-exact-badge-no-child-GI")
            call(browser.show)
            native.wait_for(
                lambda: call(lambda: browser.visible), "Companion not visible"
            )
            ImageGrab.grab(all_screens=True).save(root / "browser.png")
            stage("actual-companion-window-visible")
            script('window.fixture.store("owned");true')
            native.wait_for(
                lambda: script("window.fixture.dbValue") == "owned",
                "Storage did not settle",
            )
            call(lambda: browser._process.send({"op": "fixture-origin"}))
            origin = native.wait_for(
                lambda: report.get("view_origin"), "View origin absent"
            )
            point = script(
                '(()=>{const r=document.getElementById("input")'
                ".getBoundingClientRect();"
                "return {x:r.x+r.width/2,y:r.y+r.height/2}})()"
            )
            native.click(
                round((origin["x"] + point["x"]) * origin["scale"]),
                round((origin["y"] + point["y"]) * origin["scale"]),
            )
            native.wait_for(
                lambda: (
                    script("document.activeElement.id") == "input"
                    and any(
                        window["hwnd"] == native.user.GetForegroundWindow()
                        for window in native.windows(report["child_pids"][-1])
                    )
                ),
                "Input did not acquire native and DOM focus",
            )
            native.type_text("Docking résumé 🌱")
            native.wait_for(
                lambda: (
                    script('document.getElementById("input").value')
                    == "Docking résumé 🌱"
                ),
                "Native Unicode input did not reach browser",
            )
            native.key(0x1B)
            native.wait_for(
                lambda: not call(lambda: browser.visible),
                "Escape did not hide companion",
            )
            call(browser.show)
            native.wait_for(
                lambda: call(lambda: browser.visible),
                "Retained companion did not reopen",
            )
            stage("real-Unicode-input-Escape-and-retained-browser")
            script(
                "window.fixture.workerInit=null;Notification.requestPermission().then(()=>window.fixture.startWorker()).then(()=>window.fixture.workerInit={ok:true}).catch(e=>window.fixture.workerInit={ok:false,error:e.message});true"
            )
            answer = native.wait_for(
                lambda: script("window.fixture.workerInit"), "Worker not active", 30
            )
            assert answer["ok"], answer

            def rows():
                access, entries = NotificationListener().read(threading.Event())
                assert access == 1
                return [
                    r.summary for _k, _w, r in entries if r.summary.startswith(marker)
                ]

            report["shown"] = request("show", marker + " first")
            native.wait_for(
                lambda: rows() == [marker + " first"], "No native worker toast"
            )
            stage("persistent-worker-actual-Windows-history")
            request("show", marker + " replacement")
            native.wait_for(
                lambda: rows() == [marker + " replacement"], "Tag replacement failed"
            )
            stage("persistent-worker-tag-replacement")
            native.key(ord("N"), windows=True)
            time.sleep(0.6)
            ImageGrab.grab(all_screens=True).save(root / "worker-card.png")
            native.click(*args.notification_point)
            native.wait_for(
                lambda: any(e["event"] == "clicked" for e in request("events")),
                "Click did not reach worker",
                20,
            )
            native.wait_for(lambda: not rows(), "Click retained native toast")
            assert request("list") == []
            report["worker_events"] = request("events")
            stage("native-card-click-real-worker-handler-and-close")
            script('window.fixture.notify("' + marker + ' page");true')
            native.wait_for(
                lambda: rows() == [marker + " page"], "Page notification missing"
            )
            script("window.fixture.notification.close();true")
            native.wait_for(lambda: not rows(), "Page close retained native toast")
            stage("page-native-notification-close")
            result = []
            call(
                lambda: browser.clear_session(
                    lambda ok, error: result.append((ok, error))
                )
            )
            native.wait_for(lambda: bool(result), "Disconnect callback absent", 30)
            assert result == [(True, "")], result
            native.wait_for(
                lambda: call(lambda: browser.phase.value == "ready"),
                "Disconnected page not rebuilt",
                30,
            )
            script("window.fixture.read();true")
            storage = native.wait_for(
                lambda: script("window.fixture.storage"), "Cleared storage not readable"
            )
            assert storage == {"local": None, "cookie": "", "db": None}, storage
            stage("native-profile-destruction-and-disconnect-clears-storage")
            script(
                "window.fixture.registrationCount=null;navigator.serviceWorker.getRegistrations().then(rows=>window.fixture.registrationCount=rows.length);true"
            )
            native.wait_for(
                lambda: script("window.fixture.registrationCount") == 0,
                "Disconnect retained worker registration",
            )
            if args.hang_at_end:
                call(lambda: browser._process.send({"op": "fixture-hang"}))
                native.wait_for(
                    lambda: report.get("fixture_hung"), "Qt hold was not acknowledged"
                )
                report["pulses_before_stop"] = report["gtk_pulses"]
                stage("explicit-fixture-Qt-main-thread-hold")
            report["ok"] = True
        except Exception as error:
            errors.append(error)
            report.update(ok=False, error=repr(error))
            if (
                browser._process is not None
                and browser._ready
                and "Unicode input" in str(error)
            ):
                report["input_failure_value"] = script(
                    'document.getElementById("input").value'
                )
            print("FAILED", repr(error), flush=True)
            ImageGrab.grab(all_screens=True).save(root / "failure.png")
        finally:
            try:
                session = call(lambda: browser._process)
                owned_handles = []
                if session is not None and session._process is not None:
                    process = psutil.Process(session._process.pid)
                    owned_handles = [process, *process.children(recursive=True)]
                report["owned_native_pids"] = [process.pid for process in owned_handles]
                stop_started = time.monotonic()
                call(browser.stop)
                if session:
                    assert session.closed.wait(30), "Companion did not terminate"

                def owned():
                    return [
                        p.pid
                        for p in psutil.process_iter(["pid", "cmdline"])
                        if folder.name.casefold()
                        in " ".join(p.info["cmdline"] or []).casefold()
                    ]

                native.wait_for(
                    lambda: not owned(), "Owned Chromium processes survived", 20
                )
                native.wait_for(
                    lambda: not any(process.is_running() for process in owned_handles),
                    "Owned native process tree survived",
                    20,
                )
                report["shutdown_seconds"] = time.monotonic() - stop_started
                report["normal_shutdown"] = not args.hang_at_end
                report["forced_tree_shutdown"] = args.hang_at_end
                if args.hang_at_end:
                    assert report["gtk_pulses"] > report["pulses_before_stop"] + 5
            except Exception as error:
                errors.append(error)
                report.update(ok=False, cleanup_error=repr(error))
            finally:
                fixture.close()
                GLib.idle_add(Gtk.main_quit)

    browser.start()
    driver = threading.Thread(target=drive)
    driver.start()
    try:
        Gtk.main()
        driver.join(40)
        assert not driver.is_alive()
        assert native.workarea() == baseline and native.explorer() == explorer
    finally:
        GLib.source_remove(pulse_source)
        WindowsCompanionBrowser._packet = original_packet
        folder._finalizer.detach()
        (root / "result.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report))
    if errors:
        raise errors[0]


if __name__ == "__main__":
    main()
