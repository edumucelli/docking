"""Exercise Qt download dialogs, real transfer controls and normal process exit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
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
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires the owned Windows desktop")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    profile = tempfile.TemporaryDirectory(prefix="Docking-webengine-download-smoke-")
    os.environ.update(
        XDG_DATA_HOME=profile.name,
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

    from docking.applets.telegram.state import PROFILE as TELEGRAM
    from docking.applets.webapp.implementations.windows.companion.browser import (
        WindowsCompanionBrowser,
    )
    from docking.applets.whatsapp.state import PROFILE as WHATSAPP
    from tools.windows.visual.desktop_driver import WindowsDesktop
    from tools.windows.visual.webengine_fixture import TLSFixture

    native = WindowsDesktop()
    assert not any(row["title"] == "Docking" for row in native.windows())
    baseline, explorer = native.workarea(), native.explorer()
    service = WHATSAPP if args.service == "whatsapp" else TELEGRAM
    fixture = TLSFixture(output, Path(profile.name))
    fixture.start()
    marker = "Docking Qt résumé " + uuid.uuid4().hex[:12]
    fixture.download_name = marker + ".txt"
    owned = Path.home() / "Downloads" / ("Docking-owned-downloads-" + uuid.uuid4().hex)
    owned.mkdir()
    report = {
        "service": args.service,
        "profile_directory": Path(profile.name).name,
        "stages": [],
        "child_pids": [],
        "gtk_pulses": 0,
        "shutdown": [],
    }
    errors, pending = [], {}
    sequence = 0
    original_packet = WindowsCompanionBrowser._packet

    def packet(browser, values, lifetime):
        if browser._lifetime == lifetime:
            if values.get("event") == "fixture":
                report["child_pids"].append(values["pid"])
                assert not values["gi_imported"]
            elif values.get("event") == "fixture_origin":
                report["origin"] = values
            elif values.get("event") == "probe":
                answer = pending.get(values["id"])
                if answer is not None:
                    answer[1].append(values.get("result"))
                    answer[0].set()
        return original_packet(browser, values, lifetime)

    WindowsCompanionBrowser._packet = packet
    browser = WindowsCompanionBrowser(
        profile=service,
        on_phase=lambda *_args: None,
        on_title=lambda *_args: None,
        on_badge=lambda *_args: None,
        on_notification=lambda: None,
        on_attention_cleared=lambda: None,
        entrypoint=Path(__file__).with_name("webengine_fixture.py"),
    )

    def pulse():
        report["gtk_pulses"] += 1
        return True

    pulse_source = GLib.timeout_add(50, pulse)

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
        assert done.wait(20), "GTK did not respond"
        if failures:
            raise failures[0]
        return results[0]

    def query(source=None):
        nonlocal sequence
        sequence += 1
        done, values = threading.Event(), []
        pending[sequence] = (done, values)
        command = {
            "op": "probe" if source is not None else "fixture-downloads",
            "id": sequence,
        }
        if source is not None:
            command["source"] = source
        call(lambda: browser._process.send(command))
        assert done.wait(20), "Companion did not respond during download"
        pending.pop(sequence)
        return values[0]

    def publish(stage=None):
        if stage:
            report["stages"].append(stage)
            print(stage, flush=True)
        (output / "result.json").write_text(json.dumps(report, indent=2))

    def show():
        native.wait_for(
            lambda: call(lambda: browser.phase.value == "ready"),
            "Browser not ready",
            50,
        )
        call(browser.show)
        native.wait_for(lambda: call(lambda: browser.visible), "Browser not visible")
        call(lambda: browser._process.send({"op": "fixture-origin"}))
        native.wait_for(lambda: report.get("origin"), "View origin absent")

    def click_download():
        point = query(
            "(()=>{const r=document.getElementById('download').getBoundingClientRect();"
            "return {x:r.x+r.width/2,y:r.y+r.height/2}})()"
        )
        origin = report["origin"]
        native.click(
            round((origin["x"] + point["x"]) * origin["scale"]),
            round((origin["y"] + point["y"]) * origin["scale"]),
        )
        native.wait_for(lambda: query()["chooser"], "Save dialog absent")
        dialog = native.wait_for(
            lambda: next(
                (
                    row
                    for row in native.windows(report["child_pids"][-1])
                    if row["class"] == "#32770" and row["title"] == "Save"
                ),
                None,
            ),
            "Native Windows Save dialog absent",
        )
        native.wait_for(
            lambda: native.user.GetForegroundWindow() == dialog["hwnd"],
            "Save dialog did not acquire focus",
        )
        return dialog

    def choose(path):
        native.key(ord("N"), alt=True)
        native.key(ord("A"), control=True)
        native.type_text(str(path))
        native.key(0x0D)

    def row(name):
        return next(
            (item for item in query()["transfers"] if item["name"] == name), None
        )

    def transfer(path):
        click_download()
        choose(path)
        native.wait_for(lambda: not query()["chooser"], "Save dialog did not finish")
        return native.wait_for(lambda: row(path.name), "Transfer controls absent")

    def completed(path):
        item = native.wait_for(
            lambda: item if (item := row(path.name)) and item["finished"] else None,
            "Transfer did not finish",
            50,
        )
        assert item["state"] == "DownloadCompleted", item
        assert path.read_bytes() == fixture.download_content
        native.click(*item["cancel"])
        native.wait_for(
            lambda: row(path.name) is None, "Completed request not released"
        )
        return item

    def paused(path):
        item = transfer(path)
        assert item["pause_enabled"], item
        native.click(*item["pause"])
        item = native.wait_for(
            lambda: item if (item := row(path.name)) and item["paused"] else None,
            "Real Pause button did not pause transfer",
        )
        assert item["visible"] and item["status"].startswith("Paused"), item
        time.sleep(0.5)
        first = row(path.name)["received"]
        time.sleep(0.5)
        assert row(path.name)["received"] == first
        return item

    def roots():
        session = call(lambda: browser._process)
        process = psutil.Process(session._process.pid)
        return session, [process, *process.children(recursive=True)]

    def stopped(label):
        session, processes = roots()
        before, pulses = time.monotonic(), report["gtk_pulses"]
        call(browser.stop)
        assert session.closed.wait(20), "Companion did not close"
        native.wait_for(
            lambda: not any(process.is_running() for process in processes),
            "Download process tree survived shutdown",
            10,
        )
        elapsed = time.monotonic() - before
        assert elapsed < 5, (label, elapsed)
        assert report["gtk_pulses"] > pulses
        report["shutdown"].append(
            {
                "case": label,
                "seconds": elapsed,
                "pids": [process.pid for process in processes],
            }
        )

    def drive():
        try:
            show()

            def gtk_scale():
                window = Gtk.Window()
                try:
                    return window.get_scale_factor()
                finally:
                    window.destroy()

            report["gtk_scale"] = call(gtk_scale)
            sentinel = owned / (marker + ".txt")
            sentinel.write_bytes(b"Owned sentinel remains unchanged")
            click_download()
            choose(sentinel)
            confirmation = native.wait_for(
                lambda: next(
                    (
                        window
                        for window in native.windows(report["child_pids"][-1])
                        if "Confirm" in window["title"] and window["class"] == "#32770"
                    ),
                    None,
                ),
                "Overwrite confirmation absent",
            )
            ImageGrab.grab(all_screens=True).save(
                output / "native-overwrite-confirmation.png"
            )
            native.key(ord("N"), alt=True)
            native.wait_for(
                lambda: not native.user.IsWindow(confirmation["hwnd"]),
                "Overwrite refusal did not return to Save dialog",
            )
            native.key(0x1B)
            native.wait_for(
                lambda: not query()["chooser"], "Cancelled Save dialog remained open"
            )
            assert not query()["transfers"]
            assert sentinel.read_bytes() == b"Owned sentinel remains unchanged"
            publish("native-save-overwrite-refusal-preserves-existing-file")

            http_path = owned / (marker + " saved.txt")
            assert transfer(http_path)["scheme"] == "https"
            completed(http_path)
            report["http_sha256"] = hashlib.sha256(http_path.read_bytes()).hexdigest()
            publish("real-http-attachment-native-save-unicode-name-and-byte-exact-file")

            blob_text, blob_path = (
                "Docking owned blob · café 🌱\n",
                owned / (marker + " 🌱 blob.txt"),
            )
            query(
                "(()=>{const a=document.getElementById('download');"
                "window.fixture.blob=URL.createObjectURL(new Blob(["
                + json.dumps(blob_text)
                + "],{type:'text/plain;charset=utf-8'}));"
                "a.href=window.fixture.blob;a.download="
                + json.dumps(blob_path.name)
                + ";return true})()"
            )
            assert transfer(blob_path)["scheme"] == "blob"
            fixture_content = fixture.download_content
            fixture.download_content = blob_text.encode()
            completed(blob_path)
            fixture.download_content = fixture_content
            query(
                "(()=>{URL.revokeObjectURL(window.fixture.blob);"
                "const a=document.getElementById('download');"
                "a.removeAttribute('download');a.href="
                + json.dumps(service.origin + "fixture-download")
                + ";return true})()"
            )
            publish(
                "real-blob-attachment-native-save-preserves-unicode-name-and-content"
            )

            fixture.download_content = b"Docking owned paced download\n" * (256 * 1024)
            fixture.download_delay = 0.05
            resumed_path = owned / (marker + " resumed.txt")
            item = paused(resumed_path)
            ImageGrab.grab(all_screens=True).save(output / "native-paused-transfer.png")
            native.click(*item["pause"])
            completed(resumed_path)
            report["resumed_sha256"] = hashlib.sha256(
                resumed_path.read_bytes()
            ).hexdigest()
            publish("real-progress-pause-resume-and-completed-request-release")

            cancelled_path = owned / (marker + " cancelled.txt")
            item = paused(cancelled_path)
            native.click(*item["cancel"])
            native.wait_for(
                lambda: row(cancelled_path.name) is None, "Cancelled request retained"
            )
            native.wait_for(
                lambda: (
                    not any(
                        p.name.startswith(cancelled_path.name) for p in owned.iterdir()
                    )
                ),
                "Cancelled partial file remained",
            )
            publish("real-cancel-removes-partial-file-and-releases-transfer")

            cleared_path = owned / (marker + " cleared.txt")
            paused(cleared_path)
            _old_session, processes = roots()
            old_pid = report["child_pids"][-1]
            done, cleared = threading.Event(), []
            call(
                lambda: browser.clear_session(
                    lambda ok, error: (cleared.append((ok, error)), done.set())
                )
            )
            assert done.wait(30), "Disconnect did not finish"
            assert cleared == [(True, "")], cleared
            native.wait_for(
                lambda: not any(process.is_running() for process in processes),
                "Disconnect did not reap the original browser tree",
            )
            native.wait_for(
                lambda: (
                    report["child_pids"][-1] != old_pid
                    and call(lambda: browser.phase.value == "ready")
                ),
                "Fresh companion did not become ready",
                40,
            )
            assert not query()["transfers"]
            native.wait_for(
                lambda: (
                    not any(
                        p.name.startswith(cleared_path.name) for p in owned.iterdir()
                    )
                ),
                "Disconnected download retained partial file",
            )
            publish("disconnect-cancels-paused-download-before-replacing-profile")

            show()
            active_path = owned / (marker + " stopped.txt")
            paused(active_path)
            stopped("paused-download")
            native.wait_for(
                lambda: (
                    not any(
                        p.name.startswith(active_path.name) for p in owned.iterdir()
                    )
                ),
                "Stopped download retained partial file",
            )
            publish("normal-shutdown-cancels-paused-download-and-reaps-whole-tree")

            report.pop("origin", None)
            call(browser.start)
            show()
            click_download()
            ImageGrab.grab(all_screens=True).save(output / "native-pending-save.png")
            pulses = report["gtk_pulses"]
            assert query()["chooser"]
            time.sleep(0.5)
            assert report["gtk_pulses"] > pulses
            stopped("pending-native-save-dialog")
            publish("normal-shutdown-rejects-pending-native-save-dialog-without-force")
            assert sentinel.read_bytes() == b"Owned sentinel remains unchanged"
            assert native.workarea() == baseline and native.explorer() == explorer
            report["ok"] = True
        except Exception as error:
            errors.append(error)
            report.update(ok=False, error=repr(error))
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            try:
                session = call(lambda: browser._process)
                call(browser.stop)
                if session is not None:
                    assert session.closed.wait(25)
                shutil.rmtree(owned)
                report["owned_files_removed"] = True
            except Exception as error:
                errors.append(error)
                report.update(ok=False, cleanup_error=repr(error))
            fixture.close()
            publish()
            GLib.idle_add(Gtk.main_quit)

    browser.start()
    driver = threading.Thread(
        target=drive, name="webengine-download-input", daemon=True
    )
    driver.start()
    try:
        Gtk.main()
        driver.join(40)
        assert not driver.is_alive()
        if errors:
            raise errors[0]
    finally:
        GLib.source_remove(pulse_source)
        WindowsCompanionBrowser._packet = original_packet
        profile._finalizer.detach()
        publish()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
