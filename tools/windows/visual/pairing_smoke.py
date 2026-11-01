"""Exercise pairing prompt ownership using explicit dialogs, without a radio."""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import threading
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python in an owned test VM")
    output = args.output.resolve()
    if (output / "result.json").exists():
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    import psutil
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    from docking.applets.bluetooth.implementations.windows.pairing import (
        PairingOperation,
    )
    from docking.platform.native.windows.ffi import bind
    from tools.windows.visual.desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    baseline, explorer = native.workarea(), native.explorer()
    current: list[PairingOperation] = []
    failures, cases, ticks = [], [], [0]
    window = Gtk.Window(title="Docking pairing controller")
    window.set_default_size(340, 140)
    button = Gtk.Button(label="Cancel pairing")
    button.connect("clicked", lambda _button: current[0].cancel() if current else None)
    window.add(button)

    def destroy(_window):
        if current:
            current[0].cancel()
        Gtk.main_quit()

    window.connect("destroy", destroy)
    window.show_all()

    def pulse():
        ticks[0] += 1
        return True

    timer = GLib.timeout_add(50, pulse)
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")

    def drive():
        try:
            controller = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows()
                        if w["title"] == "Docking pairing controller"
                    ),
                    None,
                ),
                "Pairing controller did not map",
            )

            def exercise(mode):
                ceremony = (
                    mode.split("-", 1)[0]
                    if mode.startswith(("pin-", "display-", "match-"))
                    else ""
                )
                arguments = ("--ceremony", ceremony) if ceremony else ()
                operation = PairingOperation(
                    command=(
                        str(executable),
                        "-u",
                        "-m",
                        "tools.windows.visual.pairing_fixture",
                        *arguments,
                    )
                )
                current[:] = [operation]
                ready = threading.Event()
                child, values, errors = [], [], []

                def on_ready(pid, _owner):
                    child.append(psutil.Process(pid))
                    ready.set()

                def run():
                    try:
                        values.append(
                            operation.run(
                                0x112233445566,
                                0xAABBCCDDEEFF,
                                8 if mode.endswith("timeout") else 30,
                                on_ready=on_ready,
                            )
                        )
                    except Exception as exc:
                        errors.append(exc)

                worker = threading.Thread(target=run, daemon=True)
                worker.start()
                try:
                    assert ready.wait(20), "Pairing helper did not publish its owner"
                    dialog = native.wait_for(
                        lambda: next(
                            (
                                w
                                for w in native.windows(child[0].pid)
                                if w["class"] == "#32770"
                            ),
                            None,
                        ),
                        "Synthetic pairing prompt did not map",
                    )
                    before = ticks[0]
                    time.sleep(0.3)
                    assert ticks[0] > before, "GTK stopped servicing events"
                    ImageGrab.grab(all_screens=True).save(output / f"{mode}-prompt.png")
                    target = (
                        controller
                        if mode == "gtk-cancel" or mode.endswith("-stop")
                        else dialog
                    )
                    left, top, right, bottom = target["rect"]
                    native.click((left + right) / 2, (top + bottom) / 2)
                    if mode in ("accept", "reject"):
                        native.key(0x0D if mode == "accept" else 0x1B)
                    elif mode == "pin-accept":
                        from ctypes import wintypes

                        edit = bind(
                            native.user,
                            "GetDlgItem",
                            ctypes.c_void_p,
                            ctypes.c_void_p,
                            ctypes.c_int32,
                        )(dialog["hwnd"], 100)
                        rectangle = wintypes.RECT()
                        assert edit and native.user.GetWindowRect(
                            edit, ctypes.byref(rectangle)
                        )
                        native.click(
                            (rectangle.left + rectangle.right) / 2,
                            (rectangle.top + rectangle.bottom) / 2,
                        )
                        native.type_text("135790")
                        native.key(0x0D)
                    elif mode == "match-reject":
                        native.key(0x09)
                        native.key(0x0D)
                    elif mode.endswith("-accept"):
                        native.key(0x0D)
                    elif mode.endswith("-reject"):
                        native.key(0x1B)
                    worker.join(15)
                    assert not worker.is_alive(), (
                        "Owned pairing helper outlived its bound"
                    )
                    assert not errors, errors
                    assert values == [mode == "accept" or mode.endswith("-accept")], (
                        values
                    )
                    # Windows may retain a PID while another handle references
                    # the exited process. Wait on its exit, rather than PID presence.
                    exit_code = child[0].wait(timeout=2)
                    assert not native.windows(child[0].pid), (
                        "Pairing prompt survived cleanup"
                    )
                    cases.append(
                        {
                            "mode": mode,
                            "accepted": values[0],
                            "gtk_serviced": True,
                            "child_reaped": True,
                            "native_process_exit_code": exit_code,
                        }
                    )
                finally:
                    operation.cancel()
                    worker.join(10)

            for mode in (
                "accept",
                "reject",
                "gtk-cancel",
                "timeout",
                "pin-accept",
                "pin-reject",
                "pin-stop",
                "pin-timeout",
                "display-accept",
                "display-reject",
                "match-accept",
                "match-reject",
            ):
                exercise(mode)
            assert native.workarea() == baseline
            assert native.explorer() == explorer
        except Exception as exc:
            failures.append(exc)
        finally:
            GLib.idle_add(window.destroy)

    driver = threading.Thread(target=drive, daemon=True)
    driver.start()
    Gtk.main()
    GLib.source_remove(timer)
    driver.join(15)
    assert not driver.is_alive(), "Pairing fixture driver did not stop"
    report = {
        "ok": not failures,
        "cases": cases,
        "physical_pairing": False,
        "scope": "synthetic dialog; production helper IPC and ownership",
        "errors": [str(exc) for exc in failures],
    }
    (output / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if failures:
        raise failures[0]
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
