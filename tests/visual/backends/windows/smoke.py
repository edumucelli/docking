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
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from tests.visual.backends.windows.desktop_driver import WindowsDesktop


def dock_window(rows: list[dict]) -> dict | None:
    """Ignore GTK tooltip/menu popups that inherit the application's title."""
    return next(
        (
            row
            for row in rows
            if row["title"] == "Docking" and row["class"] == "gdkWindowToplevel"
        ),
        None,
    )


def open_background_menu(
    native: WindowsDesktop, dock: dict, windows: Callable[[], list[dict]]
) -> dict:
    """Send one native Ctrl-right-click and observe its mapped menu."""
    left, _top, right, bottom = dock["rect"]
    x, y = (left + right) // 2, bottom - 25
    dock_hwnd = dock["hwnd"]

    def wait_for_dock_input():
        native.wait_for(
            lambda: native.window_at(x, y) == dock_hwnd,
            "Native pointer did not reach the dock input region",
        )

    def mapped_menu():
        return next(
            (
                window
                for window in windows()
                if window["hwnd"] != dock_hwnd
                and window["title"] == "Docking"
                and window["rect"][0] >= 0
                and window["rect"][2] - window["rect"][0] > 100
            ),
            None,
        )

    native.click(x, y, right=True, control=True, before_press=wait_for_dock_input)
    return native.wait_for(mapped_menu, "Docking's native context menu did not open")


def placement_pixels(
    before, after, workarea, edge: str, dpi: int, origin=(0, 0), gap: int = 0
) -> dict:
    """Measure the fixture shelf in the independently expected desktop edge band."""
    from PIL import ImageChops

    scale = dpi / 96
    left, top, right, bottom = workarea
    band = round(160 * scale)
    box = {
        "bottom": (left, max(top, bottom - band), right, bottom),
        "top": (left, top, right, min(bottom, top + band)),
        "left": (left, top, min(right, left + band), bottom),
        "right": (max(left, right - band), top, right, bottom),
    }[edge]
    crop = tuple(value - origin[index % 2] for index, value in enumerate(box))
    difference = ImageChops.difference(before.convert("RGB"), after.convert("RGB"))
    mask = (
        difference.crop(crop).convert("L").point(lambda value: 255 if value > 20 else 0)
    )
    # AppBar reservations also resize unrelated windows behind the dock.
    red, green, blue = after.convert("RGB").crop(crop).split()
    marker = ImageChops.multiply(
        red.point(lambda value: 255 if value > 230 else 0),
        blue.point(lambda value: 255 if value > 230 else 0),
    )
    marker = ImageChops.multiply(
        marker, green.point(lambda value: 255 if value < 30 else 0)
    )
    mask = ImageChops.multiply(mask, marker)
    bounds = mask.getbbox()
    count = mask.histogram()[255]
    assert bounds is not None and count >= 1000 * scale * scale, (
        "Visible dock pixels absent"
    )
    x1, y1, x2, y2 = bounds
    horizontal = edge in {"top", "bottom"}
    center = (box[0] + (x1 + x2) / 2) if horizontal else (box[1] + (y1 + y2) / 2)
    expected = (left + right) / 2 if horizontal else (top + bottom) / 2
    distance = {
        "top": y1,
        "bottom": mask.height - y2,
        "left": x1,
        "right": mask.width - x2,
    }[edge]
    assert abs(center - expected) <= 32 * scale and distance <= 60 * scale, (
        "Dock pixels misplaced"
    )
    if gap:
        assert abs(distance - gap * scale) <= 12 * scale, (
            f"Configured dock gap absent: expected {gap * scale}, measured {distance}"
        )
    return {"edge": edge, "pixels": count, "distance": distance}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--position", choices=("bottom", "top", "left", "right"), default="bottom"
    )
    parser.add_argument("--placement-only", action="store_true")
    parser.add_argument("--edge-gap", type=int, choices=(0, 40), default=0)
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

    from tests.visual.backends.windows.desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[4]
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
                "position": args.position,
                "additional_distance_from_edge": args.edge_gap,
                **({"theme": "visual-placement"} if args.placement_only else {}),
                "hide_mode": "none",
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    if args.placement_only:
        theme = config.parent / "themes" / "visual-placement.json"
        theme.parent.mkdir(parents=True, exist_ok=True)
        theme.write_text(
            json.dumps(
                {
                    "shelf": {
                        key: [255, 0, 255, 255]
                        for key in (
                            "fill_start_color",
                            "fill_end_color",
                            "stroke_color",
                            "inner_stroke_color",
                        )
                    }
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
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowDpiAwarenessContext.argtypes = (wintypes.HWND,)
    user32.GetWindowDpiAwarenessContext.restype = ctypes.c_void_p
    user32.AreDpiAwarenessContextsEqual.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    user32.AreDpiAwarenessContextsEqual.restype = wintypes.BOOL
    user32.GetDpiForWindow.argtypes = (wintypes.HWND,)
    user32.GetDpiForWindow.restype = wintypes.UINT
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
        _x1, y1, x2, _y2 = preferences["rect"]
        # The right-aligned spin button moves when tabs widen Preferences.
        user32.SetCursorPos(x2 - 168, y1 + 194)
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

    def windows(pid: int) -> list[dict]:
        rows = []

        @callback_type
        def visit(hwnd, _data):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and user32.IsWindowVisible(hwnd):
                title = ctypes.create_unicode_buffer(512)
                window_class = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, title, 512)
                user32.GetClassNameW(hwnd, window_class, len(window_class))
                rect = wintypes.RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    rows.append(
                        {
                            "hwnd": hwnd,
                            "title": title.value,
                            "class": window_class.value,
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
    executable = (
        Path(sys.executable)
        if args.placement_only
        else Path(sys.executable).with_name("pythonw.exe")
    )
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
    backend_file = output / "backend.txt"
    startup_probe = (
        "from pathlib import Path\n"
        "from docking.launcher import prepare_display\n"
        "prepare_display()\n"
        "from docking import app\n"
        "original_start = app._start_runtime\n"
        "def start(items, model, backend):\n"
        f"    Path({str(backend_file)!r}).write_text(backend.name)\n"
        "    return original_start(items, model, backend)\n"
        "app._start_runtime = start\n"
        if args.placement_only
        else ""
    )
    if args.placement_only:
        command = [
            str(executable),
            "-c",
            startup_probe + "from docking.launcher import main; main()",
        ]
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
                + startup_probe
                + "from docking.launcher import main; main()"
            ),
            str(installed),
        ]
        working_directory = output
        report["installed_root"] = str(installed)
    if args.bundle is not None:
        from tools.windows.bundle import payload_root

        bundle = args.bundle.resolve()
        payload = payload_root(bundle)
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
        report["bundle_payload"] = str(payload)
    report["executable"] = command[0]
    child = None
    dock = None
    before = ImageGrab.grab(all_screens=True)
    if args.placement_only:
        before.save(output / "before.png")
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

                interpreter = payload / "runtime/bin/pythonw.exe"
                processes = psutil.Process(child.pid).children(recursive=True)
                application_pid = next(
                    (
                        process.pid
                        for process in processes
                        if Path(process.exe()) == interpreter
                    ),
                    child.pid,
                )
            dock = dock_window(windows(application_pid))
            if dock:
                break
            time.sleep(0.25)
        if dock is None:
            raise AssertionError("No visible native Docking window appeared")
        report["application_pid"] = application_pid
        report["per_monitor_v2"] = bool(
            user32.AreDpiAwarenessContextsEqual(
                user32.GetWindowDpiAwarenessContext(dock["hwnd"]), ctypes.c_void_p(-4)
            )
        )
        report["window_dpi"] = user32.GetDpiForWindow(dock["hwnd"])
        if not report["per_monitor_v2"]:
            raise AssertionError("Docking did not create a per-monitor-v2-aware window")
        if args.bundle is not None:
            report["application_executable"] = psutil.Process(application_pid).exe()
        user32.SetCursorPos(20, 20)
        time.sleep(2)
        resting = ImageGrab.grab(all_screens=True)
        resting.save(output / "startup.png")
        dock = next(w for w in windows(application_pid) if w["hwnd"] == dock["hwnd"])
        report["window"] = dock
        left, top, right, bottom = dock["rect"]
        if right <= left or bottom <= top:
            raise AssertionError("Docking has empty native window geometry")
        if args.placement_only:
            report["placement"] = placement_pixels(
                before,
                resting,
                [
                    desktop_before.work.left,
                    desktop_before.work.top,
                    desktop_before.work.right,
                    desktop_before.work.bottom,
                ],
                args.position,
                report["window_dpi"],
                (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)),
                args.edge_gap,
            )
            assert backend_file.read_text() == "windows", "Unexpected session backend"
            user32.PostMessageW(dock["hwnd"], 0x0010, 0, 0)
            child.wait(timeout=10)
            assert child.returncode == 0, "Dock failed normal shutdown"
            report["ok"] = True
            return
        foreground = user32.GetForegroundWindow()
        user32.SetCursorPos((left + right) // 2, bottom - 25)
        time.sleep(2)
        hover = ImageGrab.grab(all_screens=True)
        hover.save(output / "hover.png")
        report["hover_preserves_focus"] = user32.GetForegroundWindow() == foreground
        if not report["hover_preserves_focus"]:
            raise AssertionError("Hovering Docking stole keyboard focus")
        context_menu = open_background_menu(
            native, dock, lambda: windows(application_pid)
        )
        report["windows_after_menu"] = windows(application_pid)

        def painted_menu():
            frame = ImageGrab.grab(all_screens=True)
            difference = ImageChops.difference(
                hover.convert("RGB"), frame.convert("RGB")
            )
            changed = (
                difference.convert("L").point(lambda value: bool(value)).histogram()[1]
            )
            report["menu_changed_pixels"] = changed
            return frame if changed >= 1000 else None

        menu = native.wait_for(
            painted_menu,
            "The native context-menu input produced no substantial visual change",
        )
        menu.save(output / "menu.png")
        click_menu_row(context_menu, 3)
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
        click_menu_row(
            open_background_menu(native, dock, lambda: windows(application_pid)), 0
        )
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
