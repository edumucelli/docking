"""Observe real service-worker notifications through the Windows browser host."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--service", choices=("whatsapp", "telegram"), default="whatsapp"
    )
    parser.add_argument(
        "--observe",
        action="store_true",
        help="Record unavailable native delivery without failing the command",
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires the owned Windows desktop")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from docking.launcher import prepare_display
    from docking.platform.backends.windows.webview import loader_path

    os.environ["DOCKING_WEBVIEW2_LOADER"] = str(loader_path())
    profile = tempfile.TemporaryDirectory(prefix="Docking-webapp-worker-smoke-")
    os.environ["XDG_DATA_HOME"] = profile.name
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    import psutil
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    from docking.applets.notifications.windows import NotificationListener
    from docking.applets.telegram.browser import PROFILE as TELEGRAM
    from docking.applets.webapp.windows import WindowsWebAppBrowser
    from docking.applets.whatsapp.browser import PROFILE as WHATSAPP
    from tools.windows.visual.desktop_driver import WindowsDesktop
    from tools.windows.visual.webapp_fixture import WebAppPageFixture

    native = WindowsDesktop()
    assert not any(window["title"] == "Docking" for window in native.windows())
    baseline, explorer = native.workarea(), native.explorer()
    service = WHATSAPP if args.service == "whatsapp" else TELEGRAM
    fixture = WebAppPageFixture(service)
    marker = "Docking generated worker " + uuid.uuid4().hex[:8]
    report = {
        "pid": os.getpid(),
        "service": service.session_name,
        "profile_directory": Path(profile.name).name,
        "native_callbacks": 0,
        "stages": [],
    }
    errors = []
    original_ready = WindowsWebAppBrowser._browser_ready
    original_notification = WindowsWebAppBrowser._notification

    def ready(browser):
        fixture.install(browser._engine)
        original_ready(browser)

    def received(browser, event):
        report["native_callbacks"] += 1
        original_notification(browser, event)

    WindowsWebAppBrowser._browser_ready = ready
    WindowsWebAppBrowser._notification = received
    browser = WindowsWebAppBrowser(
        profile=service,
        on_phase=lambda *_args: None,
        on_title=lambda *_args: None,
        on_badge=lambda *_args: None,
        on_notification=lambda: None,
        on_attention_cleared=lambda: None,
    )

    def call(callback):
        done, results, failures = threading.Event(), [], []

        def invoke():
            try:
                results.append(callback())
            except Exception as error:
                failures.append(error)
            finally:
                done.set()
            return False

        GLib.idle_add(invoke)
        assert done.wait(20), "GTK call timed out"
        if failures:
            raise failures[0]
        return results[0]

    def script(source):
        done, results = threading.Event(), []

        def completed(text):
            results.append(json.loads(text))
            done.set()

        call(lambda: browser._engine.execute(source, completed))
        assert done.wait(20), "Browser script timed out"
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

    def rows():
        access, entries = NotificationListener().read(threading.Event())
        assert access == 1, "Notification history permission is required"
        return [
            entry.summary
            for _key, _when, entry in entries
            if entry.summary.startswith(marker)
        ]

    def owned_processes():
        result = []
        for process in psutil.process_iter(["pid", "name", "cmdline"]):
            if (
                process.info["name"] or ""
            ).lower() == "msedgewebview2.exe" and profile.name.casefold() in " ".join(
                process.info["cmdline"] or []
            ).casefold():
                result.append(process.info["pid"])
        return result

    def drive():
        try:
            native.wait_for(
                lambda: call(lambda: browser.phase.value == "ready"),
                "Page not ready",
                40,
            )
            script(
                "window.fixture.workerInit=null;Notification.requestPermission()"
                ".then(()=>window.fixture.startWorker())"
                ".then(()=>window.fixture.workerInit={ok:true})"
                ".catch(e=>window.fixture.workerInit={ok:false,error:e.message});true"
            )
            activated = native.wait_for(
                lambda: script("window.fixture.workerInit"), "No worker activation", 30
            )
            assert activated["ok"], activated
            assert fixture.worker_requests > 0
            report["stages"].append("real-worker-registered-and-active")
            report["worker_after_show"] = request("show", marker + " first")
            try:
                native.wait_for(
                    lambda: rows() == [marker + " first"], "No native worker toast", 15
                )
            except AssertionError:
                report["native_delivery"] = False
            else:
                report["native_delivery"] = True
            report["native_history"] = rows()
            report["stages"].append("native-history-observed")
            ImageGrab.grab(all_screens=True).save(output / "worker-browser.png")
            if report["native_delivery"]:
                request("show", marker + " replacement")
                native.wait_for(
                    lambda: rows() == [marker + " replacement"],
                    "Worker tag replacement failed",
                )
                report["stages"].append("worker-tag-replacement")
            request("close")
            native.wait_for(lambda: not rows(), "Worker close retained native history")
            assert request("list") == []
            report["worker_events"] = request("events")
            report["stages"].append("worker-close-and-getNotifications")
            report["ok"] = bool(report["native_delivery"])
            if not report["ok"] and not args.observe:
                raise AssertionError(
                    "Persistent worker notification did not reach Windows"
                )
        except Exception as error:
            errors.append(error)
            report.update(ok=False, error=repr(error))
        finally:
            try:
                call(browser.stop)
                native.wait_for(
                    lambda: not owned_processes(),
                    "Owned WebView2 processes survived",
                    25,
                )
                report["normal_shutdown"] = True
            except Exception as error:
                errors.append(error)
                report.update(ok=False, cleanup_error=repr(error))
            finally:
                GLib.idle_add(Gtk.main_quit)

    browser.start()
    driver = threading.Thread(target=drive)
    driver.start()
    try:
        Gtk.main()
        driver.join(45)
        assert not driver.is_alive()
        assert native.workarea() == baseline and native.explorer() == explorer
        if errors:
            raise errors[0]
    finally:
        WindowsWebAppBrowser._browser_ready = original_ready
        WindowsWebAppBrowser._notification = original_notification
        report["worker_requests"] = fixture.worker_requests
        profile._finalizer.detach()
        (output / "result.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
