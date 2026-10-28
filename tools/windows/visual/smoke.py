"""Exercise the complete Docking application on a real Windows desktop.

Save independent desktop captures and Win32 window observations. Run through
the interactive lab agent, not a Windows service or an offscreen renderer.
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
    installation = parser.add_mutually_exclusive_group()
    installation.add_argument(
        "--installed-root",
        type=Path,
        help="Exercise a wheel installed here, outside the source checkout",
    )
    installation.add_argument(
        "--bundle",
        type=Path,
        help="Exercise Docking.exe with a clean runtime environment",
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This check requires a native Windows desktop")
    from PIL import ImageChops, ImageGrab

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[3]
    config_home = output / "config"
    config = config_home / "docking" / "dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": f"applet://{name}"}
                    for name in (
                        "applications",
                        "clock",
                        "calendar",
                        "calculator",
                        "hydration",
                    )
                ],
                "icon_size": 48,
                "position": "bottom",
                "hide_mode": "none",
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    )
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
    user32.PostMessageW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("monitor", wintypes.RECT),
            ("work", wintypes.RECT),
            ("flags", wintypes.DWORD),
        ]

    user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.POINTER(MonitorInfo))
    user32.GetMonitorInfoW.restype = wintypes.BOOL

    class MouseInput(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("extra", ctypes.c_size_t),
        ]

    class KeyboardInput(ctypes.Structure):
        _fields_ = [
            ("key", wintypes.WORD),
            ("scan", wintypes.WORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("extra", ctypes.c_size_t),
        ]

    class InputData(ctypes.Union):
        _fields_: ClassVar[list[tuple[str, type[ctypes.Structure]]]] = [
            ("mouse", MouseInput),
            ("keyboard", KeyboardInput),
        ]

    class Input(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("data", InputData)]

    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT

    def key(value: int) -> None:
        events = (Input * 2)(
            Input(type=1, data=InputData(keyboard=KeyboardInput(key=value))),
            Input(type=1, data=InputData(keyboard=KeyboardInput(key=value, flags=2))),
        )
        if user32.SendInput(2, events, ctypes.sizeof(Input)) != 2:
            raise ctypes.WinError(ctypes.get_last_error())
        time.sleep(0.15)

    def open_background_menu() -> None:
        # Let GTK handle the preceding native pointer motion and update its
        # shaped input region before delivering the button events.
        time.sleep(0.3)
        events = (Input * 4)(
            Input(type=1, data=InputData(keyboard=KeyboardInput(key=0x11))),
            Input(type=0, data=InputData(mouse=MouseInput(flags=0x0008))),
            Input(type=0, data=InputData(mouse=MouseInput(flags=0x0010))),
            Input(type=1, data=InputData(keyboard=KeyboardInput(key=0x11, flags=2))),
        )
        if user32.SendInput(4, events, ctypes.sizeof(Input)) != 4:
            raise ctypes.WinError(ctypes.get_last_error())
        time.sleep(1)

    def click_menu_row(menu_window: dict, rows_from_bottom: int) -> None:
        # The baseline lab uses the default Windows GTK theme at 96 DPI. These
        # row centers were checked against the captured native menu. This is a
        # desktop acceptance test, not an arbitrary-theme menu geometry test.
        x1, _y1, x2, y2 = menu_window["rect"]
        user32.SetCursorPos((x1 + x2) // 2, y2 - 24 - 25 * rows_from_bottom)
        time.sleep(0.25)
        events = (Input * 2)(
            Input(type=0, data=InputData(mouse=MouseInput(flags=0x0002))),
            Input(type=0, data=InputData(mouse=MouseInput(flags=0x0004))),
        )
        if user32.SendInput(2, events, ctypes.sizeof(Input)) != 2:
            raise ctypes.WinError(ctypes.get_last_error())

    def set_icon_size(preferences: dict, size: int) -> None:
        x1, y1, _x2, _y2 = preferences["rect"]
        user32.SetCursorPos(x1 + 440, y1 + 194)
        events = (Input * 2)(
            Input(type=0, data=InputData(mouse=MouseInput(flags=0x0002))),
            Input(type=0, data=InputData(mouse=MouseInput(flags=0x0004))),
        )
        user32.SendInput(2, events, ctypes.sizeof(Input))
        select_all = (Input * 4)(
            Input(type=1, data=InputData(keyboard=KeyboardInput(key=0x11))),
            Input(type=1, data=InputData(keyboard=KeyboardInput(key=0x41))),
            Input(type=1, data=InputData(keyboard=KeyboardInput(key=0x41, flags=2))),
            Input(type=1, data=InputData(keyboard=KeyboardInput(key=0x11, flags=2))),
        )
        user32.SendInput(4, select_all, ctypes.sizeof(Input))
        for character in str(size):
            key(ord(character))
        key(0x0D)

    def menu_window(pid: int) -> dict:
        return next(
            w
            for w in windows(pid)
            if w["hwnd"] != dock["hwnd"]
            and w["title"] == "Docking"
            and w["rect"][0] >= 0
            and w["rect"][2] - w["rect"][0] > 100
        )

    def windows(pid: int) -> list[dict]:
        rows = []

        @callback_type
        def visit(hwnd, _data):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                title = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, title, 512)
                rect = wintypes.RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    rows.append(
                        {
                            "hwnd": hwnd,
                            "title": title.value,
                            "rect": [rect.left, rect.top, rect.right, rect.bottom],
                        }
                    )
            return True

        user32.EnumWindows(visit, 0)
        return rows

    report = {
        "platform": str(sys.getwindowsversion()),
        "input_size": ctypes.sizeof(Input),
    }
    desktop_before = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
    if not user32.GetMonitorInfoW(
        user32.MonitorFromWindow(None, 1), ctypes.byref(desktop_before)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    report["desktop_before_dock"] = [
        desktop_before.work.left,
        desktop_before.work.top,
        desktop_before.work.right,
        desktop_before.work.bottom,
    ]
    executable = Path(sys.executable).with_name("pythonw.exe")
    report["executable"] = str(executable)
    env = {
        **os.environ,
        "XDG_CONFIG_HOME": str(config_home),
        "XDG_CACHE_HOME": str(output / "cache"),
        "DOCKING_LOG_LEVEL": "DEBUG",
    }
    # Exercise Docking's own DLL bootstrap rather than inheriting the agent's.
    env.pop("PYGI_DLL_PATH", None)
    command = [str(executable), str(repo / "run.py")]
    working_directory = repo
    if args.installed_root is not None:
        installed = args.installed_root.resolve()
        assert (installed / "docking/__init__.py").is_file()
        env["PYTHONPATH"] = str(installed)
        command = [
            str(executable),
            "-c",
            (
                "import sys; from pathlib import Path; import docking; "
                "origin=Path(docking.__file__).resolve(); "
                "assert origin.is_relative_to(Path(sys.argv[1]).resolve()), origin; "
                "from docking.launcher import main; main()"
            ),
            str(installed),
        ]
        working_directory = output
        report["installed_root"] = str(installed)
    if args.bundle is not None:
        bundle = args.bundle.resolve()
        command = [str(bundle / "Docking.exe")]
        working_directory = output
        for name in tuple(env):
            if name.startswith(("PYTHON", "GTK_", "GDK_", "GI_", "GST_")) or name in {
                "MSYSTEM",
                "MINGW_PREFIX",
                "PYGI_DLL_PATH",
                "XDG_DATA_DIRS",
                "FONTCONFIG_PATH",
            }:
                env.pop(name)
        env["PATH"] = str(Path(os.environ["WINDIR"]) / "System32")
        report["bundle"] = str(bundle)
    child = None
    dock = None
    try:
        with (output / "docking.log").open("w", encoding="utf-8") as log:
            child = subprocess.Popen(
                command,
                cwd=working_directory,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        deadline = time.monotonic() + 40
        application_pid = child.pid
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise AssertionError(
                    f"Docking exited during startup: {child.returncode}"
                )
            if args.bundle is not None:
                import psutil

                interpreter = args.bundle.resolve() / "runtime/bin/pythonw.exe"
                processes = psutil.Process(child.pid).children()
                application_pid = next(
                    (
                        process.pid
                        for process in processes
                        if Path(process.exe()) == interpreter
                    ),
                    child.pid,
                )
            dock = next(
                (w for w in windows(application_pid) if w["title"] == "Docking"), None
            )
            if dock:
                break
            time.sleep(0.25)
        if dock is None:
            raise AssertionError("No visible native Docking window appeared")
        report["application_pid"] = application_pid
        user32.SetCursorPos(20, 20)
        time.sleep(2)
        resting = ImageGrab.grab(all_screens=True)
        resting.save(output / "startup.png")
        dock = next(w for w in windows(application_pid) if w["hwnd"] == dock["hwnd"])
        report["window"] = dock
        left, top, right, bottom = dock["rect"]
        if right <= left or bottom <= top:
            raise AssertionError("Docking has empty native window geometry")
        foreground = user32.GetForegroundWindow()
        user32.SetCursorPos((left + right) // 2, bottom - 25)
        time.sleep(2)
        hover = ImageGrab.grab(all_screens=True)
        hover.save(output / "hover.png")
        report["hover_preserves_focus"] = user32.GetForegroundWindow() == foreground
        if not report["hover_preserves_focus"]:
            raise AssertionError("Hovering Docking stole keyboard focus")
        open_background_menu()
        menu = ImageGrab.grab(all_screens=True)
        menu.save(output / "menu.png")
        difference = ImageChops.difference(hover.convert("RGB"), menu.convert("RGB"))
        changed = (
            difference.convert("L").point(lambda value: bool(value)).histogram()[1]
        )
        report["menu_changed_pixels"] = changed
        report["windows_after_menu"] = windows(application_pid)
        if changed < 1000:
            raise AssertionError(
                "The native context-menu input produced no substantial visual change"
            )
        click_menu_row(menu_window(application_pid), 3)
        deadline = time.monotonic() + 10
        preferences = None
        while time.monotonic() < deadline:
            preferences = next(
                (w for w in windows(application_pid) if w["title"] == "Preferences"),
                None,
            )
            if preferences:
                break
            time.sleep(0.25)
        report["preferences"] = preferences
        if preferences is None:
            raise AssertionError("The menu did not open a native Preferences window")
        time.sleep(1)
        ImageGrab.grab(all_screens=True).save(output / "preferences.png")
        set_icon_size(preferences, 64)
        time.sleep(1)
        report["persisted_icon_size"] = json.loads(config.read_text("utf-8"))[
            "icon_size"
        ]
        if report["persisted_icon_size"] != 64:
            raise AssertionError("Changing icon size in Preferences did not persist")
        ImageGrab.grab(all_screens=True).save(output / "preferences-edited.png")
        user32.PostMessageW(preferences["hwnd"], 0x0010, 0, 0)
        time.sleep(1)
        dock = next(w for w in windows(application_pid) if w["hwnd"] == dock["hwnd"])
        left, top, right, bottom = dock["rect"]
        monitor_info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        monitor_handle = user32.MonitorFromWindow(dock["hwnd"], 2)
        if not user32.GetMonitorInfoW(monitor_handle, ctypes.byref(monitor_info)):
            raise ctypes.WinError(ctypes.get_last_error())
        work = monitor_info.work
        report["window_after_resize"] = dock
        report["workarea"] = [work.left, work.top, work.right, work.bottom]
        if not (
            left >= desktop_before.work.left
            and top >= desktop_before.work.top
            and right <= desktop_before.work.right
            and bottom <= desktop_before.work.bottom
        ):
            raise AssertionError(
                "Resizing the dock placed it behind Explorer's taskbar"
            )
        ImageGrab.grab(all_screens=True).save(output / "resized.png")
        user32.SetCursorPos((left + right) // 2, bottom - 25)
        open_background_menu()
        click_menu_row(menu_window(application_pid), 0)
        report["shutdown_method"] = "menu_quit"
        child.wait(timeout=10)
        report["ok"] = True
    except Exception as exc:
        report["ok"] = False
        report["error"] = repr(exc)
        ImageGrab.grab(all_screens=True).save(output / "failure.png")
        raise
    finally:
        report["shutdown_forced"] = False
        if child is not None and child.poll() is None:
            # A Gtk.Menu owns a nested event loop and an input grab. Dismiss it
            # as a user would before asking the toplevel window to close.
            key(0x1B)
            time.sleep(0.5)
            if dock is not None:
                user32.PostMessageW(dock["hwnd"], 0x0010, 0, 0)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                report["shutdown_forced"] = True
                if args.bundle is not None:
                    import psutil

                    for process in psutil.Process(child.pid).children(recursive=True):
                        if Path(process.exe()).is_relative_to(args.bundle.resolve()):
                            process.terminate()
                            process.wait(timeout=10)
                child.terminate()
                child.wait(timeout=10)
        if child is not None:
            report["shutdown_exit_code"] = child.returncode
        if report.get("ok") and (
            report["shutdown_forced"] or report.get("shutdown_exit_code") != 0
        ):
            report["ok"] = False
            report["error"] = "Docking did not shut down cleanly"
        (output / "result.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, indent=2))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
