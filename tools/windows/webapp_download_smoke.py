"""Check native WebView2 downloads using owned files and actual pointer input."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
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
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from docking.launcher import prepare_display
    from docking.platform.backends.windows.webview import loader_path

    os.environ["DOCKING_WEBVIEW2_LOADER"] = str(loader_path())
    profile = tempfile.TemporaryDirectory(prefix="Docking-webapp-download-smoke-")
    os.environ["XDG_DATA_HOME"] = profile.name
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    import psutil
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    from docking.applets.telegram.browser import PROFILE as TELEGRAM
    from docking.applets.webapp.windows import WindowsWebAppBrowser
    from docking.applets.whatsapp.browser import PROFILE as WHATSAPP
    from docking.platform.backends.windows.com import (
        GUID,
        HRESULT,
        method,
        object_result,
        query,
        value,
    )
    from docking.platform.backends.windows.ffi import bind
    from docking.platform.backends.windows.winrt import check
    from tools.windows.desktop_driver import WindowsDesktop
    from tools.windows.webapp_fixture import WebAppPageFixture

    native = WindowsDesktop()
    assert not any(window["title"] == "Docking" for window in native.windows())
    baseline, explorer = native.workarea(), native.explorer()
    service = WHATSAPP if args.service == "whatsapp" else TELEGRAM
    fixture = WebAppPageFixture(service)
    marker = "Docking generated résumé " + uuid.uuid4().hex
    fixture.download_filename = marker + ".txt"
    fixture.download_content = b"Docking owned native download\n"
    report = {
        "pid": os.getpid(),
        "service": service.session_name,
        "profile_directory": Path(profile.name).name,
        "records": [],
        "stages": [],
    }
    shared, errors = {}, []
    original_ready = WindowsWebAppBrowser._browser_ready

    def ready(browser):
        fixture.install(browser._engine)
        engine = browser._engine
        view = query(
            engine._scope,
            engine.core,
            GUID.parse("20d02d59-6df2-42dc-bd06-f98a694b1302"),
        )

        def received(event):
            operation = object_result(engine._scope, event, 3)
            record = {
                "states": [],
                "initial_name": Path(engine.text(event, 6)).name,
                "uri_scheme": engine.text(operation, 9).partition(":")[0],
            }
            report["records"].append(record)
            shared["operation"] = operation

            def changed(_event):
                state = value(operation, 16, ctypes.c_int32).value
                record["states"].append(state)
                if state == 2:
                    shared["path"] = Path(engine.text(operation, 15))
                    record["final_name"] = shared["path"].name
                elif state == 1:
                    record["reason"] = value(operation, 17, ctypes.c_int32).value
                    record["can_resume"] = bool(
                        value(operation, 21, ctypes.c_int32).value
                    )

            engine.subscribe(
                operation, 7, "81336594-7ede-4ba9-bf71-acf0a95b58dd", changed
            )
            if shared.pop("pause_next", False):

                def pause():
                    shared.pop("pause_source", None)
                    check(method(operation, 19, HRESULT)(operation))
                    return False

                shared["pause_source"] = GLib.idle_add(pause)

        # Observe the SDK event only. The production host keeps WebView2's
        # destination choice, native dialog and file-safety behavior intact.
        engine.subscribe(view, 75, "efedc989-c396-41ca-83f7-07f845a55724", received)
        original_ready(browser)

    WindowsWebAppBrowser._browser_ready = ready
    browser = WindowsWebAppBrowser(
        profile=service,
        on_phase=lambda *_args: None,
        on_title=lambda *_args: None,
        on_badge=lambda *_args: None,
        on_notification=lambda: None,
        on_attention_cleared=lambda: None,
    )
    browser.start()

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

    def click_download():
        point = script(
            "(()=>{const r=document.getElementById('download').getBoundingClientRect();"
            "return {x:(r.x+r.width/2)*devicePixelRatio,"
            "y:(r.y+r.height/2)*devicePixelRatio}})()"
        )
        from ctypes.wintypes import POINT

        position = POINT(round(point["x"]), round(point["y"]))
        convert = bind(
            native.user,
            "ClientToScreen",
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.POINTER(POINT),
        )
        assert convert(call(lambda: browser._engine._parent), ctypes.byref(position))
        native.click(position.x, position.y)

    def owned_processes():
        return [
            process.pid
            for process in psutil.process_iter(["pid", "cmdline"])
            if profile.name.casefold()
            in " ".join(process.info["cmdline"] or []).casefold()
        ]

    def stop_browser():
        source = shared.pop("pause_source", 0)
        if source:
            GLib.source_remove(source)
        browser.stop()

    def drive():
        files_owned = False
        downloads = Path(
            GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
            or Path.home() / "Downloads"
        )
        try:
            native.wait_for(
                lambda: call(lambda: browser.phase.value == "ready"),
                "Generated page did not become ready",
                40,
            )
            call(browser.show)
            time.sleep(0.5)
            sentinel = downloads / fixture.download_filename
            with sentinel.open("xb") as stream:
                files_owned = True
                stream.write(b"Preserved owned sentinel")
            paths = {sentinel}
            for index in range(2):
                click_download()
                native.wait_for(
                    lambda index=index: len(report["records"]) > index,
                    "DownloadStarting missing after actual pointer input",
                )
                record = report["records"][index]
                native.wait_for(
                    lambda record=record: (
                        2 in record["states"] or 1 in record["states"]
                    ),
                    "Native download did not finish",
                )
                assert 2 in record["states"], record
                path = shared["path"]
                assert path.parent == downloads and path.name.startswith(marker)
                assert path.read_bytes() == fixture.download_content
                assert sentinel.read_bytes() == b"Preserved owned sentinel"
                paths.add(path)
            assert len(paths) == 3
            report["stages"].append("unicode-native-downloads-preserve-existing-file")
            ImageGrab.grab(all_screens=True).save(output / "native-downloads.png")

            blob_text = "Docking generated attachment · café\n"
            blob_name = marker + " blob.txt"
            assert script(
                "(()=>{const a=document.getElementById('download');"
                "window.fixture.blob=URL.createObjectURL(new Blob(["
                + json.dumps(blob_text)
                + "],{type:'text/plain;charset=utf-8'}));"
                "a.href=window.fixture.blob;a.download="
                + json.dumps(blob_name)
                + ";return true})()"
            )
            click_download()
            native.wait_for(lambda: len(report["records"]) == 3, "Blob download absent")
            record = report["records"][2]
            native.wait_for(
                lambda: 2 in record["states"] or 1 in record["states"],
                "Blob attachment did not finish",
            )
            assert 2 in record["states"] and record["uri_scheme"] == "blob", record
            path = shared["path"]
            assert path.parent == downloads and path.name == blob_name
            assert path.read_bytes() == blob_text.encode()
            assert script(
                "(()=>{URL.revokeObjectURL(window.fixture.blob);"
                "const a=document.getElementById('download');"
                "a.removeAttribute('download');"
                "a.href="
                + json.dumps(service.origin + "fixture-download")
                + ";return true})()"
            )
            report["stages"].append("native-blob-attachment-preserves-unicode")

            fixture.download_content = b"Docking owned large download\n" * (1024 * 1024)
            shared["pause_next"] = True
            click_download()
            native.wait_for(
                lambda: len(report["records"]) == 4, "Paused download absent"
            )
            record = report["records"][3]
            native.wait_for(
                lambda: 1 in record["states"] or 2 in record["states"],
                "Native pause did not settle",
            )
            assert 1 in record["states"] and record.get("can_resume"), record
            # The native state event precedes the progress dialog's paint.
            time.sleep(0.5)
            ImageGrab.grab(all_screens=True).save(output / "native-paused-download.png")
            call(
                lambda: check(
                    method(shared["operation"], 20, HRESULT)(shared["operation"])
                )
            )
            native.wait_for(
                lambda: 2 in record["states"], "Resumed download did not finish", 30
            )
            path = shared["path"]
            assert path.parent == downloads and path.name.startswith(marker)
            assert path.read_bytes() == fixture.download_content
            report["resumed_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            report["stages"].append("native-pause-resume-preserves-generated-file")

            shared["pause_next"] = True
            click_download()
            native.wait_for(
                lambda: len(report["records"]) == 5, "Shutdown download absent"
            )
            record = report["records"][4]
            native.wait_for(
                lambda: 1 in record["states"] or 2 in record["states"],
                "Shutdown pause did not settle",
            )
            assert 1 in record["states"] and record.get("can_resume"), record
            call(stop_browser)
            native.wait_for(
                lambda: not owned_processes(),
                "Browser left owned WebView2 processes after paused-download shutdown",
                25,
            )
            report["stages"].append(
                "paused-download-shutdown-releases-native-processes"
            )
            report["ok"] = True
        except Exception as error:
            errors.append(error)
            report.update(ok=False, error=repr(error))
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            try:
                call(stop_browser)
                # These names contain this run's random marker. Preserve every
                # other file in the actual native Downloads directory.
                if files_owned:
                    for path in downloads.glob(marker + "*"):
                        if path.is_file():
                            path.unlink()
            except Exception as error:
                errors.append(error)
                report.update(ok=False, cleanup_error=repr(error))
            finally:
                GLib.idle_add(Gtk.main_quit)

    driver = threading.Thread(target=drive)
    driver.start()
    try:
        Gtk.main()
        driver.join(45)
        assert not driver.is_alive()
        assert native.workarea() == baseline and native.explorer() == explorer
        if errors:
            raise errors[0]
        report["normal_shutdown"] = True
    except Exception as error:
        report.update(ok=False, error=repr(error))
        raise
    finally:
        WindowsWebAppBrowser._browser_ready = original_ready
        profile._finalizer.detach()
        (output / "result.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
