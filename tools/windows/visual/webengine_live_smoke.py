"""Check anonymous service login through the production Qt browser entry point."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--service", choices=("whatsapp", "telegram"), default="whatsapp"
    )
    parser.add_argument("--show-before-login", action="store_true")
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument("--chromium-user-agent", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires the owned Windows desktop")
    if args.chromium_user_agent and not args.diagnostic:
        parser.error("The user-agent experiment requires --diagnostic")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    profile = tempfile.TemporaryDirectory(prefix="Docking-webengine-live-smoke-")
    os.environ.update(
        XDG_DATA_HOME=profile.name,
        XDG_CACHE_HOME=str(output / "cache"),
        DOCKING_WEBENGINE_PYTHON=str(args.python),
    )
    os.environ.pop("QTWEBENGINE_CHROMIUM_FLAGS", None)
    os.environ["DOCKING_BROWSER_LIVE_CHROMIUM_UA"] = (
        "1" if args.chromium_user_agent else "0"
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    import psutil
    from gi.repository import GLib, Gtk

    from docking.applets.telegram.state import PROFILE as TELEGRAM
    from docking.applets.webapp.implementations.windows.companion.browser import (
        WindowsCompanionBrowser,
    )
    from docking.applets.whatsapp.state import PROFILE as WHATSAPP
    from tools.windows.visual.desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    assert not any(row["title"] == "Docking" for row in native.windows())
    baseline, explorer = native.workarea(), native.explorer()
    service = WHATSAPP if args.service == "whatsapp" else TELEGRAM
    report = {
        "service": args.service,
        "profile_directory": Path(profile.name).name,
        "phases": [],
        "stages": [],
        "gtk_pulses": 0,
    }
    errors = []
    probe_done, probe_values = threading.Event(), []
    original_packet = WindowsCompanionBrowser._packet

    def packet(browser, values, lifetime):
        if browser._lifetime == lifetime and values.get("event") == "service_probe":
            probe_values.append(values["summary"])
            probe_done.set()
        return original_packet(browser, values, lifetime)

    if args.diagnostic:
        WindowsCompanionBrowser._packet = packet
    browser = WindowsCompanionBrowser(
        profile=service,
        on_phase=lambda phase, _error: report["phases"].append(phase.value),
        on_title=lambda _title: None,
        on_badge=lambda *_args: None,
        on_notification=lambda: None,
        on_attention_cleared=lambda: None,
        entrypoint=Path(__file__).with_name("webengine_live_probe.py")
        if args.diagnostic
        else None,
    )
    report.update(diagnostic=args.diagnostic, show_before_login=args.show_before_login)
    report["chromium_user_agent"] = args.chromium_user_agent

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

    def publish(stage=None):
        if stage:
            report["stages"].append(stage)
            print(stage, flush=True)
        (output / "result.json").write_text(json.dumps(report, indent=2))

    def pulse():
        report["gtk_pulses"] += 1
        return True

    pulse_source = GLib.timeout_add(50, pulse)

    def login():
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            if call(lambda: browser.phase.value == "login_required"):
                return
            if args.diagnostic and call(lambda: browser._ready):
                probe_done.clear()
                call(lambda: browser._process.send({"op": "inspect-service", "id": 1}))
                assert probe_done.wait(20), "Live diagnostic did not reply"
                report["summary"] = probe_values[-1]
                publish()
            time.sleep(1)
        raise AssertionError("Actual service did not expose its anonymous login state")

    def drive():
        processes, session = [], None
        try:
            before = time.monotonic()
            if args.show_before_login:
                call(browser.show)
            login()
            report["initial_login_seconds"] = time.monotonic() - before
            if not args.show_before_login:
                assert not call(lambda: browser.visible)
            session = call(lambda: browser._process)
            root = psutil.Process(session._process.pid)
            processes = [root, *root.children(recursive=True)]
            report["child_pid"] = root.pid
            publish("real-service-anonymous-login")
            call(browser.show)
            native.wait_for(
                lambda: call(lambda: browser.visible), "Browser did not show"
            )
            window = native.wait_for(
                lambda: next(
                    (
                        row
                        for row in native.windows(root.pid)
                        if row["title"] == service.name
                    ),
                    None,
                ),
                "Real service browser window absent",
            )
            # Focus only the owned caption; never interact with the login code.
            native.click(window["rect"][0] + 100, window["rect"][1] + 12)
            native.wait_for(
                lambda: native.user.GetForegroundWindow() == window["hwnd"],
                "Owned browser did not acquire native focus",
            )
            assert call(lambda: browser.phase.value) == "login_required"
            native.key(0x1B)
            native.wait_for(
                lambda: not call(lambda: browser.visible), "Escape did not hide service"
            )
            call(browser.show)
            native.wait_for(
                lambda: call(lambda: browser.visible), "Retained service did not show"
            )
            assert session._process.pid == root.pid
            publish("real-service-window-focus-escape-and-retained-show")
            call(browser.reload)
            login()
            assert session._process.pid == root.pid
            publish("reload-recovers-real-anonymous-login")
            report["ok"] = True
        except Exception as error:
            errors.append(error)
            report.update(ok=False, error=repr(error))
        finally:
            try:
                session = call(lambda: browser._process)
                if session is not None and session._process is not None:
                    root = psutil.Process(session._process.pid)
                    processes = [root, *root.children(recursive=True)]
                before = time.monotonic()
                call(browser.stop)
                if session is not None:
                    assert session.closed.wait(25), "Production browser did not close"
                native.wait_for(
                    lambda: not any(row.is_running() for row in processes),
                    "Owned real-service process tree survived",
                    10,
                )
                report["shutdown_seconds"] = time.monotonic() - before
                report["owned_native_pids"] = [row.pid for row in processes]
                assert native.workarea() == baseline and native.explorer() == explorer
                # Real service profiles can contain anonymous pairing state.
                # Remove them after their exact owned process trees have exited.
                shutil.rmtree(profile.name)
                report["profile_removed"] = True
            except Exception as error:
                errors.append(error)
                report.update(ok=False, cleanup_error=repr(error))
            publish()
            GLib.idle_add(Gtk.main_quit)

    browser.start()
    driver = threading.Thread(target=drive, name="webengine-real-service", daemon=True)
    driver.start()
    try:
        Gtk.main()
        driver.join(40)
        assert not driver.is_alive()
        if errors:
            raise errors[0]
    finally:
        GLib.source_remove(pulse_source)
        profile._finalizer.detach()
        WindowsCompanionBrowser._packet = original_packet
        publish()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
