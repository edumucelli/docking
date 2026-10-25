"""Exercise native Search hotkeys through the complete Windows dock lifecycle.

Runs the normal application composition and GTK main loop with isolated
settings. A driver thread sends Win32 keyboard input while Notepad is foreground.
GTK inspection and Preferences actions run on the main loop. The check covers
focus, typing, repeat/lock keys, capture/cancel/rebind, conflicts, disable/enable,
persistence and release after normal application shutdown.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import ClassVar


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(output / "config")
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    repo = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.platform.backends.windows.hotkeys import (
        MOD_ALT,
        MOD_CONTROL,
        Message,
        WindowsHotkeyAPI,
    )
    from docking.search.services.global_shortcuts import GlobalShortcutsState

    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes = (
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    )
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user.IsWindow.argtypes = (wintypes.HWND,)
    user.IsWindowVisible.argtypes = (wintypes.HWND,)
    user.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
    user.GetForegroundWindow.restype = wintypes.HWND
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
    user.SendInput.restype = wintypes.UINT

    def send(events):
        values = [
            Input(type=1, data=Data(keyboard=Keyboard(key=key, scan=scan, flags=flags)))
            for key, scan, flags in events
        ]
        assert user.SendInput(
            len(values), (Input * len(values))(*values), ctypes.sizeof(Input)
        ) == len(values)

    def click(x, y):
        user.SetCursorPos(int(x), int(y))
        time.sleep(0.3)
        values = (Input * 2)(
            Input(type=0, data=Data(mouse=Mouse(flags=2))),
            Input(type=0, data=Data(mouse=Mouse(flags=4))),
        )
        assert user.SendInput(2, values, ctypes.sizeof(Input)) == 2
        time.sleep(0.2)

    def key(value):
        send([(value, 0, 0), (value, 0, 2)])
        time.sleep(0.15)

    def chord(value):
        send(
            [
                (0x11, 0, 0),
                (0x12, 0, 0),
                (value, 0, 0),
                (value, 0, 2),
                (0x12, 0, 2),
                (0x11, 0, 2),
            ]
        )
        time.sleep(0.2)

    def type_text(text):
        events = []
        for character in text:
            events.extend([(0, ord(character), 4), (0, ord(character), 6)])
        send(events)

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
                user.GetWindowRect(hwnd, ctypes.byref(rect))
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

    def wait_for(predicate, message, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.1)
        raise AssertionError(message)

    def available(value):
        native = WindowsHotkeyAPI()
        try:
            native.register(MOD_CONTROL | MOD_ALT, value)
        except OSError as exc:
            if exc.winerror == 1409:
                return False
            raise
        native.unregister()
        return True

    assert not any(
        w["title"] == "Docking" or "notepad" in w["title"].lower() for w in windows()
    ), "An existing dock or Notepad must be closed before the owned fixture starts"
    assert available(0x20), "The default hotkey is already owned by another application"
    config_home = output / "config"
    config_path = config_home / "docking/dock.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "pinned": [{"kind": "applet", "target": "applet://search"}],
                "icon_size": 48,
                "hide_mode": "none",
                "position": "bottom",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
                "global_search_enabled": True,
                "global_search_shortcut": "CTRL+ALT+space",
            }
        ),
        encoding="utf-8",
    )
    os.environ["XDG_CONFIG_HOME"] = str(config_home)
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    shared = {}
    ready = threading.Event()
    report = {
        "message_size": ctypes.sizeof(Message),
        "input_size": ctypes.sizeof(Input),
    }
    errors = []
    original_build = app.build_dock_window

    def build(**kwargs):
        ui = original_build(**kwargs)
        shared.update(
            ui=ui,
            config=kwargs["config"],
            registry=kwargs["application_registry"],
            launcher=kwargs["application_launcher"],
        )
        ready.set()
        return ui

    app.build_dock_window = build

    def ui_call(callback):
        completed = threading.Event()
        result, failures = [], []

        def invoke():
            try:
                result.append(callback())
            except Exception as exc:
                failures.append(exc)
            finally:
                completed.set()
            return False

        GLib.idle_add(invoke)
        assert completed.wait(15), "GTK did not service a queued test observation"
        if failures:
            raise failures[0]
        return result[0]

    def drive():
        editor = None
        competitor = None
        locks_toggled = False
        try:
            assert ready.wait(90), "The complete dock UI did not start"
            ui = shared["ui"]
            search = ui.search

            def state():
                return ui_call(
                    lambda: (
                        search.shortcut_status.state if search.shortcut_status else None
                    )
                )

            wait_for(
                lambda: state() is GlobalShortcutsState.BOUND,
                "Native registration did not bind",
            )
            report["backend"] = ui_call(lambda: type(search._global_shortcuts).__name__)
            assert report["backend"] == "WindowsGlobalShortcutsService"
            assert not available(0x20), "The dock did not own its declared hotkey"
            app_id = ui_call(
                lambda: next(
                    a.desktop_id
                    for a in shared["registry"].snapshot()
                    if a.name.lower() == "notepad"
                )
            )
            assert ui_call(lambda: shared["launcher"].launch(app_id))
            editor = wait_for(
                lambda: next(
                    (w for w in windows() if w["title"].endswith(" - Notepad")), None
                ),
                "Owned Notepad did not appear",
                40,
            )
            report["notepad"] = editor
            x1, y1, x2, y2 = editor["rect"]
            click((x1 + x2) // 2, (y1 + y2) // 2)
            wait_for(
                lambda: user.GetForegroundWindow() == editor["hwnd"],
                "Notepad was not foreground",
            )

            def search_window():
                return next(
                    (
                        w
                        for w in windows()
                        if w["pid"] == os.getpid() and w["title"] == "Docking Search"
                    ),
                    None,
                )

            def open_search(value):
                chord(value)
                window = wait_for(
                    search_window, "The native hotkey did not open Search"
                )
                wait_for(
                    lambda: user.GetForegroundWindow() == window["hwnd"],
                    "Search did not gain foreground focus",
                )
                return window

            def hide_search(value):
                chord(value)
                wait_for(
                    lambda: not search_window(), "The shortcut did not hide Search"
                )

            report["search"] = open_search(0x20)
            type_text("notepad")
            wait_for(
                lambda: (
                    ui_call(lambda: search.window.search_entry.get_text()) == "notepad"
                ),
                "Hotkey activation did not give the Search entry typing focus",
            )
            wait_for(
                lambda: ui_call(
                    lambda: any(
                        "notepad" in r.title.lower()
                        for r in search._coordinator.snapshot().results
                    )
                ),
                "Search did not publish its Notepad result",
            )
            time.sleep(0.3)
            ImageGrab.grab(all_screens=True).save(output / "native-hotkey-search.png")
            report["typing_focus"] = True
            hide_search(0x20)
            wait_for(
                lambda: user.GetForegroundWindow() == editor["hwnd"],
                "Hiding Search did not return focus to Notepad",
            )
            send([(0x11, 0, 0), (0x12, 0, 0), (0x20, 0, 0)])
            try:
                wait_for(search_window, "Held hotkey did not open Search")
                for _ in range(8):
                    send([(0x20, 0, 0)])
                    time.sleep(0.05)
                assert search_window(), "Key repeat toggled Search back off"
            finally:
                send([(0x20, 0, 2), (0x12, 0, 2), (0x11, 0, 2)])
            hide_search(0x20)
            report["repeat_suppressed"] = True
            key(0x14)
            key(0x90)
            locks_toggled = True
            open_search(0x20)
            hide_search(0x20)
            key(0x14)
            key(0x90)
            locks_toggled = False
            report["lock_keys"] = True

            def show_capture():
                ui.settings.show()
                button = ui.settings._global_search_shortcut_entry
                child = button
                parent = child.get_parent()
                while parent is not None:
                    if isinstance(parent, Gtk.Stack):
                        parent.set_visible_child(child)
                        break
                    child, parent = parent, parent.get_parent()

            ui_call(show_capture)
            time.sleep(0.4)

            def begin_capture():
                button = ui.settings._global_search_shortcut_entry
                parent = button.get_parent()
                while parent is not None:
                    if isinstance(parent, Gtk.ScrolledWindow):
                        adjustment = parent.get_vadjustment()
                        adjustment.set_value(
                            adjustment.get_upper() - adjustment.get_page_size()
                        )
                        break
                    parent = parent.get_parent()
                button.clicked()

            ui_call(begin_capture)
            wait_for(
                lambda: state() is GlobalShortcutsState.STOPPED,
                "Capture did not suspend the native binding",
            )
            assert available(0x20), "Capture kept the previous hotkey grabbed"
            chord(ord("K"))
            wait_for(
                lambda: (
                    ui_call(lambda: shared["config"].global_search_shortcut)
                    == "CTRL+ALT+k"
                ),
                "Native keyboard capture did not update the preferred shortcut",
            )
            wait_for(
                lambda: state() is GlobalShortcutsState.BOUND,
                "Captured hotkey did not rebind",
            )
            assert available(0x20) and not available(ord("K")), (
                "Rebind did not release the old key and own the new one"
            )
            report["rebound"] = ui_call(
                lambda: search.shortcut_status.binding.trigger_description
            )
            time.sleep(0.3)
            ImageGrab.grab(all_screens=True).save(
                output / "native-shortcut-preferences.png"
            )
            # Cancel capture while another owner takes the released old binding.
            ui_call(lambda: ui.settings._global_search_shortcut_entry.clicked())
            wait_for(
                lambda: state() is GlobalShortcutsState.STOPPED,
                "Second capture did not suspend",
            )
            competitor = WindowsHotkeyAPI()
            competitor.register(MOD_CONTROL | MOD_ALT, ord("K"))
            key(0x1B)
            wait_for(
                lambda: state() is GlobalShortcutsState.ERROR,
                "A real registration conflict was not reported",
            )
            assert ui_call(search.shortcut_status_summary) == "Conflict"
            assert ui_call(lambda: search.shortcut_status.binding) is None
            ImageGrab.grab(all_screens=True).save(
                output / "native-shortcut-conflict.png"
            )
            report["conflict_reported"] = True
            competitor.unregister()
            competitor = None
            ui_call(lambda: ui.settings._global_search_shortcut_entry.clicked())
            key(0x1B)
            wait_for(
                lambda: state() is GlobalShortcutsState.BOUND,
                "Capture cancellation did not recover after conflict release",
            )
            for _ in range(5):
                ui_call(lambda: ui.settings._global_search_shortcut_entry.clicked())
                assert available(ord("K"))
                key(0x1B)
                wait_for(
                    lambda: state() is GlobalShortcutsState.BOUND,
                    "Repeated capture cancellation lost the binding",
                )
            report["capture_cancel_cycles"] = 5
            assert (
                sum(t.name == "windows-search-hotkey" for t in threading.enumerate())
                == 1
            )
            ui_call(lambda: ui.settings._global_search_switch.set_active(False))
            assert available(ord("K"))
            report["disabled_releases_binding"] = True
            ui_call(lambda: ui.settings._global_search_switch.set_active(True))
            wait_for(
                lambda: state() is GlobalShortcutsState.BOUND,
                "Reenabling Search did not restore the binding",
            )
            ui_call(ui.settings.close)
            user.SetForegroundWindow(editor["hwnd"])
            wait_for(
                lambda: user.GetForegroundWindow() == editor["hwnd"],
                "Notepad did not regain foreground",
            )
            open_search(ord("K"))
            hide_search(ord("K"))
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            assert saved["global_search_shortcut"] == "CTRL+ALT+k"
            assert saved["global_search_enabled"] is True
            report["persisted_shortcut"] = saved["global_search_shortcut"]
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report["ok"] = False
            report["error"] = repr(exc)
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            if competitor is not None:
                competitor.unregister()
            if locks_toggled:
                key(0x14)
                key(0x90)
            if editor is not None:
                for window in windows():
                    if window["pid"] == editor["pid"]:
                        user.PostMessageW(window["hwnd"], 0x10, 0, 0)
            if ready.is_set():
                ui_call(lambda: shared["ui"].settings.close())
                ui_call(lambda: shared["ui"].search.hide())
                ui_call(lambda: shared["ui"].window.close())

    driver = threading.Thread(target=drive, name="windows-shortcut-smoke", daemon=True)
    driver.start()
    try:
        app.main()
        driver.join(timeout=10)
        assert not driver.is_alive(), "Test driver survived normal application shutdown"
        assert available(0x20) and available(ord("K")), (
            "Normal shutdown retained a registered hotkey"
        )
        report["shutdown_releases_bindings"] = True
        assert not any(t.name == "windows-search-hotkey" for t in threading.enumerate())
        if errors:
            raise errors[0]
    finally:
        (output / "result.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
