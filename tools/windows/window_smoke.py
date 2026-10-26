"""Test native window tracking through real dock clicks on Windows Notepad.

Uses independent Win32 observations rather than the backend under test to
confirm launch, minimize, restore, activation, close and clean dock shutdown.
The baseline is the lab's default GTK theme at 96 DPI.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import ClassVar


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This check requires native Windows")
    from PIL import ImageGrab

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo))
    from docking.launcher import prepare_display

    prepare_display()
    from docking.platform.applications.registry import ApplicationRegistry

    registry = ApplicationRegistry()
    registry.refresh()
    app = next(a for a in registry.snapshot() if a.name.lower() == "notepad")
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    )
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user.IsWindowVisible.argtypes = (wintypes.HWND,)
    user.IsWindow.argtypes = (wintypes.HWND,)
    user.IsIconic.argtypes = (wintypes.HWND,)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
    user.PostMessageW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    class Mouse(ctypes.Structure):
        _fields_ = [
            ("x", wintypes.LONG),
            ("y", wintypes.LONG),
            ("data", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("extra", ctypes.c_size_t),
        ]

    class Keyboard(ctypes.Structure):
        _fields_ = [
            ("key", wintypes.WORD),
            ("scan", wintypes.WORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("extra", ctypes.c_size_t),
        ]

    class Data(ctypes.Union):
        _fields_: ClassVar[list[tuple[str, type[ctypes.Structure]]]] = [
            ("mouse", Mouse),
            ("keyboard", Keyboard),
        ]

    class Input(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("data", Data)]

    user.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)

    def click(x: int, y: int, *, right: bool = False, control: bool = False) -> None:
        user.SetCursorPos(x, y)
        time.sleep(0.3)
        events = []
        if control:
            events.append(Input(type=1, data=Data(keyboard=Keyboard(key=0x11))))
        for flag in (8, 16) if right else (2, 4):
            events.append(Input(type=0, data=Data(mouse=Mouse(flags=flag))))
        if control:
            events.append(
                Input(type=1, data=Data(keyboard=Keyboard(key=0x11, flags=2)))
            )
        if user.SendInput(
            len(events), (Input * len(events))(*events), ctypes.sizeof(Input)
        ) != len(events):
            raise ctypes.WinError(ctypes.get_last_error())

    def windows() -> list[dict]:
        rows = []

        @callback_type
        def visit(hwnd, _data):
            if user.IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                title = ctypes.create_unicode_buffer(1024)
                user.GetWindowTextW(hwnd, title, len(title))
                rect = wintypes.RECT()
                if user.GetWindowRect(hwnd, ctypes.byref(rect)):
                    rows.append(
                        {
                            "hwnd": hwnd,
                            "pid": pid.value,
                            "title": title.value,
                            "rect": [rect.left, rect.top, rect.right, rect.bottom],
                        }
                    )
            return True

        user.EnumWindows(visit, 0)
        return rows

    def wait_for(predicate, message: str, seconds: float = 15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            if child.poll() is not None:
                raise AssertionError(f"Docking exited: {child.returncode}")
            time.sleep(0.25)
        raise AssertionError(message)

    def click_last_menu_row() -> None:
        menu = wait_for(
            lambda: next(
                (
                    w
                    for w in windows()
                    if w["pid"] == child.pid
                    and w["title"] == "Docking"
                    and w["hwnd"] != dock["hwnd"]
                    and w["rect"][0] >= 0
                    and w["rect"][2] - w["rect"][0] > 100
                ),
                None,
            ),
            "No visible native menu appeared",
        )
        x1, _y1, x2, y2 = menu["rect"]
        click((x1 + x2) // 2, y2 - 24)

    assert not any("notepad" in w["title"].lower() for w in windows()), (
        "Notepad is already open; do not duplicate or close an unrelated instance"
    )
    config_home = output / "config"
    config = config_home / "docking" / "dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [{"kind": "app", "target": app.desktop_id}],
                "icon_size": 48,
                "hide_mode": "none",
                "position": "bottom",
                "show_recent_apps": False,
                "show_recent_docs_in_menu": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_CACHE_HOME": str(output / "cache"),
        "DOCKING_LOG_LEVEL": "DEBUG",
    }
    report = {"application_id": app.desktop_id}
    notepad = None
    with (output / "docking.log").open("w", encoding="utf-8") as log:
        child = subprocess.Popen(
            [str(Path(sys.executable).with_name("pythonw.exe")), str(repo / "run.py")],
            cwd=repo,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    try:
        dock = wait_for(
            lambda: next(
                (
                    w
                    for w in windows()
                    if w["pid"] == child.pid and w["title"] == "Docking"
                ),
                None,
            ),
            "No native Docking window appeared",
            40,
        )
        time.sleep(2)
        dock = next(w for w in windows() if w["hwnd"] == dock["hwnd"])
        x1, _y1, x2, y2 = dock["rect"]
        x, y = (x1 + x2) // 2, y2 - 25
        ImageGrab.grab(all_screens=True).save(output / "startup.png")
        click(x, y)
        notepad = wait_for(
            lambda: next(
                (w for w in windows() if "notepad" in w["title"].lower()), None
            ),
            "Clicking the pinned application did not launch Notepad",
            40,
        )
        report["notepad"] = notepad
        time.sleep(3)
        notepad = max(
            (w for w in windows() if "notepad" in w["title"].lower()),
            key=lambda w: (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1]),
        )
        report["notepad"] = notepad
        report["launch_activated"] = user.GetForegroundWindow() == notepad["hwnd"]
        ImageGrab.grab(all_screens=True).save(output / "launched.png")
        wait_for(
            lambda: user.GetForegroundWindow() == notepad["hwnd"],
            "Launching Notepad from the dock did not activate its window",
        )
        report["launch_activated"] = True
        click(x, y)
        wait_for(
            lambda: user.IsIconic(notepad["hwnd"]),
            "Clicking the running icon did not minimize Notepad",
        )
        report["minimized_by_dock"] = True
        ImageGrab.grab(all_screens=True).save(output / "minimized.png")
        time.sleep(1)
        click(x, y)
        wait_for(
            lambda: (
                not user.IsIconic(notepad["hwnd"])
                and user.GetForegroundWindow() == notepad["hwnd"]
            ),
            "Clicking the minimized icon did not restore and activate Notepad",
        )
        report["restored_by_dock"] = True
        time.sleep(1)
        ImageGrab.grab(all_screens=True).save(output / "restored.png")
        click(x, y, right=True)
        time.sleep(1)
        ImageGrab.grab(all_screens=True).save(output / "window-menu.png")
        click_last_menu_row()
        wait_for(
            lambda: not user.IsWindow(notepad["hwnd"]),
            "The dock's Close action did not close Notepad",
        )
        report["closed_by_dock"] = True
        time.sleep(1)
        ImageGrab.grab(all_screens=True).save(output / "closed.png")
        click(x, y, right=True, control=True)
        time.sleep(1)
        click_last_menu_row()
        child.wait(timeout=10)
        assert child.returncode == 0, "Docking did not exit cleanly"
        report["ok"] = True
    except Exception as exc:
        report["ok"] = False
        report["error"] = repr(exc)
        ImageGrab.grab(all_screens=True).save(output / "failure.png")
        raise
    finally:
        report["shutdown_forced"] = child.poll() is None
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        if notepad is not None and user.IsWindow(notepad["hwnd"]):
            user.PostMessageW(notepad["hwnd"], 0x10, 0, 0)
        (output / "result.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
