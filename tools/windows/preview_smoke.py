"""Exercise native Windows hover, menu and Search window previews.

Run with the lab's baseline display. The tool owns one blank Notepad window and
an isolated dock configuration; existing Notepad windows cause it to stop.
Desktop screenshots establish that a real thumbnail replaces the icon fallback.
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
    import numpy as np
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
    user.ShowWindowAsync.argtypes = (wintypes.HWND, ctypes.c_int)
    user.IsIconic.argtypes = (wintypes.HWND,)
    user.IsZoomed.argtypes = (wintypes.HWND,)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.WindowFromPoint.argtypes = (wintypes.POINT,)
    user.WindowFromPoint.restype = wintypes.HWND
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

    def key(value: int, *, control: bool = False) -> None:
        events = []
        if control:
            events.append(Input(type=1, data=Data(keyboard=Keyboard(key=0x11))))
        events.extend(
            [
                Input(type=1, data=Data(keyboard=Keyboard(key=value))),
                Input(type=1, data=Data(keyboard=Keyboard(key=value, flags=2))),
            ]
        )
        if control:
            events.append(
                Input(type=1, data=Data(keyboard=Keyboard(key=0x11, flags=2)))
            )
        assert user.SendInput(
            len(events), (Input * len(events))(*events), ctypes.sizeof(Input)
        ) == len(events)
        time.sleep(0.1)

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

    assert not any("notepad" in w["title"].lower() for w in windows()), (
        "Notepad is already open; do not duplicate or close an unrelated instance"
    )
    config_home = output / "config"
    config = config_home / "docking" / "dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "app", "target": app.desktop_id},
                    {"kind": "applet", "target": "applet://search"},
                ],
                "icon_size": 48,
                "window_preview_thumbnails_enabled": True,
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
        x, y = (x1 + x2) // 2 - 30, y2 - 25
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
        user.SetCursorPos(900, 400)
        time.sleep(0.5)
        user.SetCursorPos(x, y)
        preview = wait_for(
            lambda: next(
                (
                    w
                    for w in windows()
                    if w["pid"] == child.pid
                    and w["title"] == "Docking"
                    and w["hwnd"] != dock["hwnd"]
                    and w["rect"][0] >= 0
                    and w["rect"][3] - w["rect"][1] > 100
                    and w["rect"][2] - w["rect"][0] > 100
                ),
                None,
            ),
            "No native hover preview appeared",
            20,
        )
        report["preview"] = preview
        box = tuple(preview["rect"])

        def white_pixels():
            crop = np.asarray(ImageGrab.grab(all_screens=True).convert("RGB").crop(box))
            return int(np.all(crop[:, :, :3] > 230, axis=2).sum())

        report["thumbnail_white_pixels"] = wait_for(
            lambda: n if (n := white_pixels()) > 7000 else None,
            "The preview never replaced its fallback icon with Notepad content",
            20,
        )
        assert user.GetForegroundWindow() == notepad["hwnd"], (
            "Showing a preview stole keyboard focus"
        )
        report["hover_preserves_focus"] = True
        ImageGrab.grab(all_screens=True).save(output / "preview.png")
        px1, py1, px2, py2 = preview["rect"]
        px, py = (px1 + px2) // 2, (py1 + py2) // 2
        user.SetCursorPos(px, py)
        time.sleep(0.4)
        user.ShowWindowAsync(notepad["hwnd"], 6)
        wait_for(lambda: user.IsIconic(notepad["hwnd"]), "The fixture did not minimize")
        time.sleep(0.7)
        assert white_pixels() > 7000, "The minimized preview lost its last native image"
        ImageGrab.grab(all_screens=True).save(output / "minimized-preview.png")
        click(px, py)
        wait_for(
            lambda: (
                not user.IsIconic(notepad["hwnd"])
                and user.GetForegroundWindow() == notepad["hwnd"]
            ),
            "Clicking the thumbnail did not restore and activate Notepad",
        )
        report["thumbnail_restores_and_activates"] = True
        ImageGrab.grab(all_screens=True).save(output / "thumbnail-activated.png")
        user.SetCursorPos(900, 400)
        time.sleep(0.5)
        click(x, y, right=True)
        time.sleep(1.5)
        ImageGrab.grab(all_screens=True).save(output / "window-menu-thumbnail.png")
        key(0x1B)
        time.sleep(0.5)
        click(900, 400)
        user.ShowWindowAsync(notepad["hwnd"], 3)
        wait_for(
            lambda: user.IsZoomed(notepad["hwnd"]),
            "Notepad did not maximize for click-through verification",
        )
        user.SetCursorPos(1010, 650)
        time.sleep(0.5)
        headroom = user.WindowFromPoint(wintypes.POINT(1010, 650))
        headroom_pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(headroom, ctypes.byref(headroom_pid))
        report["transparent_headroom_hit_pid"] = headroom_pid.value
        assert headroom_pid.value == notepad["pid"], (
            "Transparent animation headroom intercepted input"
        )
        time.sleep(0.5)
        sx = (x1 + x2) // 2 + 30
        user.SetCursorPos(sx, y)
        time.sleep(0.5)
        hit = user.WindowFromPoint(wintypes.POINT(sx, y))
        hit_pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hit, ctypes.byref(hit_pid))
        report["search_hit_before_click"] = {
            "hwnd": hit,
            "pid": hit_pid.value,
            "dock_pid": child.pid,
        }
        assert hit_pid.value == child.pid, (
            "The hollow icon hit area fell through the dock"
        )
        click(sx, y)
        search = wait_for(
            lambda: next(
                (
                    w
                    for w in windows()
                    if w["pid"] == child.pid and w["title"] == "Docking Search"
                ),
                None,
            ),
            "The Search applet did not open the native palette",
            20,
        )
        wait_for(
            lambda: user.GetForegroundWindow() == search["hwnd"],
            "Search did not gain typing focus",
        )
        key(0x41, control=True)
        for character in "untitled":
            key(ord(character.upper()))
        time.sleep(1)
        for _ in range(4):
            key(0x26)
        ImageGrab.grab(all_screens=True).save(output / "search-results.png")
        key(0x50, control=True)
        time.sleep(2)
        assert user.GetForegroundWindow() == search["hwnd"], (
            "Refreshing the Search preview changed keyboard focus"
        )
        ImageGrab.grab(all_screens=True).save(output / "search-window-preview.png")
        report["search_preview_preserves_focus"] = True
        report["search"] = next(w for w in windows() if w["hwnd"] == search["hwnd"])
        key(0x1B)
        key(0x1B)
        wait_for(
            lambda: not any(w["hwnd"] == search["hwnd"] for w in windows()),
            "Search did not close after Escape",
        )
        user.SetCursorPos(900, 400)
        user.PostMessageW(notepad["hwnd"], 0x10, 0, 0)
        wait_for(
            lambda: not user.IsWindow(notepad["hwnd"]),
            "The blank fixture did not close",
        )
        user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
        child.wait(timeout=10)
        assert child.returncode == 0, "The dock did not exit normally"
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
