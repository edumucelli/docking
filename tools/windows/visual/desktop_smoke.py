"""Observe AppBar reservation and dock visibility on the real Windows desktop.

The baseline is one 1280x800 display at 96 DPI. A blank Notepad window is the
owned application fixture; screenshots and independent Win32 calls verify
behavior without reading Docking's internal hide state. Existing Notepad
windows cause the check to stop before launching or closing anything.
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
    parser.add_argument(
        "--phase", choices=("reservation", "visibility", "all"), default="all"
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This check requires native Windows")
    from PIL import ImageGrab

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[3]
    user = ctypes.WinDLL("user32", use_last_error=True)
    dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    )
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
    user.IsWindowVisible.argtypes = (wintypes.HWND,)
    user.IsWindow.argtypes = (wintypes.HWND,)
    user.IsIconic.argtypes = (wintypes.HWND,)
    user.IsZoomed.argtypes = (wintypes.HWND,)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
    user.ShowWindowAsync.argtypes = (wintypes.HWND, ctypes.c_int)
    user.PostMessageW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    user.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
    user.MonitorFromWindow.restype = wintypes.HMONITOR
    dwm.DwmGetWindowAttribute.argtypes = (
        wintypes.HWND,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
    )

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("monitor", wintypes.RECT),
            ("work", wintypes.RECT),
            ("flags", wintypes.DWORD),
        ]

    user.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.POINTER(MonitorInfo))

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

    def rect_values(rect):
        return [rect.left, rect.top, rect.right, rect.bottom]

    def workarea():
        info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        assert user.GetMonitorInfoW(user.MonitorFromWindow(None, 1), ctypes.byref(info))
        return rect_values(info.work)

    def windows():
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
                            "rect": rect_values(rect),
                        }
                    )
            return True

        user.EnumWindows(visit, 0)
        return rows

    child = None
    dock = None
    notepad = None
    report = {"baseline_workarea": workarea(), "reservation": [], "visibility": []}

    def checkpoint():
        (output / "result.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )

    def wait_for(predicate, message, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            if child is not None and child.poll() is not None:
                raise AssertionError(f"Docking exited unexpectedly: {child.returncode}")
            time.sleep(0.2)
        raise AssertionError(message)

    def click_document():
        user.SetCursorPos(900, 400)
        events = (Input * 2)(
            Input(type=0, data=Data(mouse=Mouse(flags=2))),
            Input(type=0, data=Data(mouse=Mouse(flags=4))),
        )
        assert user.SendInput(2, events, ctypes.sizeof(Input)) == 2
        wait_for(
            lambda: user.GetForegroundWindow() == notepad["hwnd"],
            "Notepad did not become foreground after native input",
        )

    def maximize():
        user.ShowWindowAsync(notepad["hwnd"], 3)
        wait_for(
            lambda: (
                user.IsZoomed(notepad["hwnd"]) and not user.IsIconic(notepad["hwnd"])
            ),
            "Notepad did not maximize",
        )
        time.sleep(0.7)
        click_document()

    def native_client_bounds():
        client = wintypes.RECT()
        assert user.GetClientRect(notepad["hwnd"], ctypes.byref(client))
        origin = wintypes.POINT(client.left, client.top)
        assert user.ClientToScreen(notepad["hwnd"], ctypes.byref(origin))
        return [
            origin.x,
            origin.y,
            origin.x + client.right - client.left,
            origin.y + client.bottom - client.top,
        ]

    def minimize():
        user.ShowWindowAsync(notepad["hwnd"], 6)
        wait_for(lambda: user.IsIconic(notepad["hwnd"]), "Notepad did not minimize")
        user.SetCursorPos(900, 400)
        # Dismiss shell tooltips left by an earlier desktop interaction before
        # recording the bare-desktop reference. Pointer movement alone can
        # leave an Explorer tooltip visible after the foreground app minimizes.
        events = (Input * 2)(
            Input(type=0, data=Data(mouse=Mouse(flags=2))),
            Input(type=0, data=Data(mouse=Mouse(flags=4))),
        )
        assert user.SendInput(2, events, ctypes.sizeof(Input)) == 2
        time.sleep(1)

    def launch(position, mode):
        nonlocal child, dock
        assert child is None
        folder = output / f"{mode}-{position}"
        config_home = folder / "config"
        config = config_home / "docking" / "dock.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            json.dumps(
                {
                    "pinned": [{"kind": "applet", "target": "applet://clock"}],
                    "icon_size": 48,
                    "hide_mode": mode,
                    "position": position,
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
            "XDG_CACHE_HOME": str(folder / "cache"),
            "DOCKING_LOG_LEVEL": "DEBUG",
        }
        with (folder / "docking.log").open("w", encoding="utf-8") as log:
            child = subprocess.Popen(
                [
                    str(Path(sys.executable).with_name("pythonw.exe")),
                    str(repo / "run.py"),
                ],
                cwd=repo,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        dock = wait_for(
            lambda: next(
                (
                    w
                    for w in windows()
                    if w["pid"] == child.pid
                    and w["title"] == "Docking"
                    and w["rect"][2] - w["rect"][0] > 100
                ),
                None,
            ),
            "Docking did not map its native window",
            45,
        )
        time.sleep(2)
        dock = next(w for w in windows() if w["hwnd"] == dock["hwnd"])
        return folder

    def stop():
        nonlocal child, dock
        if child is None:
            return
        process, window = child, dock
        child = None
        dock = None
        if process.poll() is None:
            if window is not None:
                user.PostMessageW(window["hwnd"], 0x10, 0, 0)
            try:
                exit_code = process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
                raise AssertionError(
                    "Normal dock shutdown timed out; terminated only the owned process"
                ) from None
        else:
            exit_code = process.returncode
        assert exit_code == 0, f"Docking exited with {exit_code}"
        wait_for(
            lambda: workarea() == report["baseline_workarea"],
            "Docking did not restore the original desktop work area",
        )

    assert not any("notepad" in w["title"].lower() for w in windows()), (
        "An unrelated Notepad window is already open"
    )
    assert not any(
        w["title"] in ("Docking", "Docking reservation") for w in windows()
    ), "An unrelated dock is already open"
    assert report["baseline_workarea"] == [0, 0, 1280, 752], (
        "This pixel check requires the documented single-display baseline"
    )
    try:
        subprocess.Popen(
            [str(Path(os.environ["SYSTEMROOT"]) / "System32" / "notepad.exe")],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        wait_for(
            lambda: any("notepad" in w["title"].lower() for w in windows()),
            "The owned Notepad fixture did not open",
            40,
        )
        time.sleep(3)
        notepad = max(
            (w for w in windows() if "notepad" in w["title"].lower()),
            key=lambda w: (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1]),
        )
        report["notepad"] = notepad
        maximize()
        report["maximized_client_margins"] = [
            actual - available
            for actual, available in zip(
                native_client_bounds(), report["baseline_workarea"], strict=True
            )
        ]
        if args.phase in ("all", "reservation"):
            for edge in ("bottom", "top", "left", "right"):
                minimize()
                folder = launch(edge, "none")
                area = workarea()
                before = report["baseline_workarea"]
                changed = {"left": 0, "top": 1, "right": 2, "bottom": 3}[edge]
                assert all(area[i] == before[i] for i in range(4) if i != changed), (
                    edge,
                    area,
                )
                thickness = abs(area[changed] - before[changed])
                assert 40 <= thickness <= 80, (edge, thickness, area)
                maximize()
                frame = wintypes.RECT()
                assert (
                    dwm.DwmGetWindowAttribute(
                        notepad["hwnd"], 9, ctypes.byref(frame), ctypes.sizeof(frame)
                    )
                    == 0
                )
                bounds = rect_values(frame)
                # Modern Notepad extends its client rectangle into invisible
                # resize borders. Compare with its own maximized baseline so
                # those native margins cannot masquerade as reserved space.
                client_bounds = native_client_bounds()
                expected = [
                    value + margin
                    for value, margin in zip(
                        area, report["maximized_client_margins"], strict=True
                    )
                ]
                assert client_bounds == expected, (edge, area, client_bounds, expected)
                ImageGrab.grab(all_screens=True).save(folder / "maximized.png")
                row = {
                    "edge": edge,
                    "workarea": area,
                    "resting_thickness": thickness,
                    "maximized_frame": bounds,
                    "maximized_client": client_bounds,
                    "dock": dock,
                }
                stop()
                row["restored_workarea"] = workarea()
                report["reservation"].append(row)
                checkpoint()
        if args.phase in ("all", "visibility"):
            maximize()
            time.sleep(1)
            reference_maximized = ImageGrab.grab(all_screens=True).convert("RGB")
            reference_maximized.save(output / "reference-maximized.png")
            minimize()
            reference_desktop = ImageGrab.grab(all_screens=True).convert("RGB")
            reference_desktop.save(output / "reference-desktop.png")
            box = (460, 698, 820, 744)

            def different_pixels(reference):
                current = ImageGrab.grab(all_screens=True).convert("RGB").crop(box)
                baseline = reference.crop(box)
                left, right = current.tobytes(), baseline.tobytes()
                return sum(
                    max(
                        abs(a - b)
                        for a, b in zip(left[i : i + 3], right[i : i + 3], strict=True)
                    )
                    > 12
                    for i in range(0, len(left), 3)
                )

            for mode in (
                "always-on-top",
                "autohide",
                "dodge-active",
                "window-dodge",
                "intelligent",
                "dodge-maximized",
            ):
                maximize()
                folder = launch("bottom", mode)
                click_document()
                time.sleep(1.5)
                assert workarea() == report["baseline_workarea"], (mode, workarea())
                visible = mode == "always-on-top"
                check = (lambda n: n > 500) if visible else (lambda n: n < 100)
                pixels = wait_for(
                    lambda check=check: (
                        (n if n else -1)
                        if check(n := different_pixels(reference_maximized))
                        else None
                    ),
                    f"Unexpected maximized visibility in {mode}",
                )
                ImageGrab.grab(all_screens=True).save(folder / "maximized.png")
                row = {
                    "mode": mode,
                    "maximized_changed_pixels": max(0, pixels),
                    "workarea": workarea(),
                }
                user.SetCursorPos(640, 750)
                wait_for(
                    lambda: different_pixels(reference_maximized) > 500,
                    f"The edge did not reveal the dock in {mode}",
                )
                time.sleep(0.6)
                ImageGrab.grab(all_screens=True).save(folder / "edge-revealed.png")
                assert user.GetForegroundWindow() == notepad["hwnd"], (
                    f"Edge reveal stole focus in {mode}"
                )
                row["edge_reveal_preserves_focus"] = True
                user.SetCursorPos(900, 400)
                minimize()
                visible = mode != "autohide"
                check = (lambda n: n > 500) if visible else (lambda n: n < 100)
                pixels = wait_for(
                    lambda check=check: (
                        (n if n else -1)
                        if check(n := different_pixels(reference_desktop))
                        else None
                    ),
                    f"Unexpected minimized visibility in {mode}",
                )
                ImageGrab.grab(all_screens=True).save(folder / "minimized.png")
                row["minimized_changed_pixels"] = max(0, pixels)
                stop()
                report["visibility"].append(row)
                checkpoint()
        report["ok"] = True
    except Exception as error:
        report["ok"] = False
        report["error"] = repr(error)
        ImageGrab.grab(all_screens=True).save(output / "failure.png")
        raise
    finally:
        try:
            stop()
        finally:
            if notepad is not None and user.IsWindow(notepad["hwnd"]):
                user.PostMessageW(notepad["hwnd"], 0x10, 0, 0)
            report["final_workarea"] = workarea()
            checkpoint()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
