"""Trace native Search activation at startup from desktop, taskbar or Start.

Runs the complete application. Native HWND and GUI-thread observations must
confirm foreground ownership and typing; GTK focus-in alone is insufficient.
Input comes from a separate process so the dock cannot inherit its injector's
foreground permission. Use --same-process-input only to compare diagnostics.
The Start menu's English native window is named Start or Search on this lab.
"""

from __future__ import annotations


def main():
    import argparse
    import ctypes
    import json
    import os
    import subprocess
    import sys
    import threading
    import time
    from ctypes import wintypes as w
    from pathlib import Path

    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--origin", choices=("desktop", "taskbar", "start"), required=True)
    p.add_argument("--delay", type=float, default=0)
    p.add_argument("--same-process-input", action="store_true")
    a = p.parse_args()
    if sys.platform != "win32":
        p.error("Requires native Windows Python")
    a.output.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(a.output / "config")
    os.environ["XDG_CACHE_HOME"] = str(a.output / "cache")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib
    from PIL import ImageGrab

    import docking.app as app
    from docking.search.controller import GlobalSearchController
    from docking.search.services.global_shortcuts import GlobalShortcutsState
    from docking.search.ui.window import SearchWindow
    from tools.windows_desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    baseline = native.workarea()
    assert not any(row["title"] == "Docking" for row in native.windows())
    user = native.user
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentThreadId.restype = w.DWORD
    gtk_thread = kernel.GetCurrentThreadId()

    class GUI(ctypes.Structure):
        _fields_ = (
            [("size", w.DWORD), ("flags", w.DWORD)]
            + [
                (name, w.HWND)
                for name in ("active", "focus", "capture", "menu", "move", "caret")
            ]
            + [("rect", w.RECT)]
        )

    user.GetGUIThreadInfo.argtypes = (w.DWORD, ctypes.POINTER(GUI))
    user.GetGUIThreadInfo.restype = w.BOOL
    start = time.monotonic()
    events = []
    report = {
        "origin": a.origin,
        "delay": a.delay,
        "pid": os.getpid(),
        "gtk_thread": gtk_thread,
        "input_process": "same" if a.same_process_input else "separate",
    }
    shared = {}
    bound = threading.Event()
    errors = []

    def hwnd_info(hwnd):
        if not hwnd:
            return None
        title = ctypes.create_unicode_buffer(256)
        cls = ctypes.create_unicode_buffer(256)
        pid = w.DWORD()
        user.GetWindowTextW(hwnd, title, len(title))
        user.GetClassNameW(hwnd, cls, len(cls))
        tid = user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return {
            "hwnd": int(hwnd),
            "title": title.value,
            "class": cls.value,
            "pid": pid.value,
            "thread": tid,
        }

    def gui(tid):
        state = GUI(size=ctypes.sizeof(GUI))
        if not user.GetGUIThreadInfo(tid, ctypes.byref(state)):
            return None
        return {
            "flags": state.flags,
            **{
                name: int(getattr(state, name) or 0)
                for name in ("active", "focus", "capture", "menu")
            },
        }

    def record(kind, **data):
        events.append(
            {
                "t": round(time.monotonic() - start, 4),
                "event": kind,
                "foreground": hwnd_info(user.GetForegroundWindow()),
                "foreground_gui": gui(0),
                "gtk_gui": gui(gtk_thread),
                **data,
            }
        )

    callback_type = ctypes.WINFUNCTYPE(
        None, w.HANDLE, w.DWORD, w.HWND, w.LONG, w.LONG, w.DWORD, w.DWORD
    )
    user.SetWinEventHook.argtypes = (
        w.DWORD,
        w.DWORD,
        w.HMODULE,
        callback_type,
        w.DWORD,
        w.DWORD,
        w.DWORD,
    )
    user.SetWinEventHook.restype = w.HANDLE
    user.UnhookWinEvent.argtypes = (w.HANDLE,)

    @callback_type
    def event(_hook, _event, hwnd, _object, _child, _thread, tick):
        record("foreground-event", window=hwnd_info(hwnd), tick=tick)

    hook = user.SetWinEventHook(3, 3, None, event, 0, 0, 0)
    assert hook
    native.key(0x1B)
    if a.origin == "desktop":
        native.click(1100, 100)
    else:
        native.key(0x5B)
        native.wait_for(
            lambda: (
                hwnd_info(user.GetForegroundWindow())
                and hwnd_info(user.GetForegroundWindow())["title"]
                in ("Start", "Search")
            ),
            "Native Start menu did not open",
        )
        if a.origin == "taskbar":
            native.key(0x1B)
    record("origin-ready")
    cfg = a.output / "config/docking/dock.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://clock"},
                    {"kind": "applet", "target": "applet://calculator"},
                ],
                "icon_size": 48,
                "hide_mode": "none",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
                "global_search_enabled": True,
            }
        )
    )
    original_build = app.build_dock_window
    original_runtime = app._start_runtime
    original_present = SearchWindow.present
    original_status = GlobalSearchController._on_shortcut_status

    def build(**kwargs):
        ui = original_build(**kwargs)
        shared["ui"] = ui
        for widget, label in [(ui.window, "dock"), (ui.search.window.window, "search")]:
            for signal in ("map-event", "focus-in-event", "focus-out-event"):
                widget.connect(
                    signal,
                    lambda _w, _e, label=label, signal=signal: (
                        record(label + "." + signal) or False
                    ),
                )
        record("ui-built")
        return ui

    def runtime(*args):
        record("runtime.before")
        result = original_runtime(*args)
        record("runtime.after")
        return result

    def present(self, **kwargs):
        record("search.present.before")
        result = original_present(self, **kwargs)
        record("search.present.after")
        return result

    def status(self, state):
        result = original_status(self, state)
        record("shortcut.status", state=state.state.value)
        if state.state is GlobalShortcutsState.BOUND:
            bound.set()
        return result

    app.build_dock_window = build
    app._start_runtime = runtime
    SearchWindow.present = present
    GlobalSearchController._on_shortcut_status = status

    def call(fn):
        done = threading.Event()
        values = []

        def invoke():
            try:
                values.append(fn())
            finally:
                done.set()
            return False

        GLib.idle_add(invoke)
        assert done.wait(15)
        return values[0]

    def drive():
        try:
            assert bound.wait(90)
            record("activate.before")
            if a.same_process_input:
                if a.origin == "start":
                    native.key(0x1B)
                if a.delay:
                    time.sleep(a.delay)
                native.key(0x20, control=True, alt=True)
            else:
                repository = str(Path(__file__).resolve().parent.parent)
                program = (
                    "import os,sys,time\n"
                    f"sys.path.insert(0, {repository!r})\n"
                    "from tools.windows_desktop_driver import WindowsDesktop\n"
                    "native=WindowsDesktop()\n"
                    + ("native.key(0x1B)\n" if a.origin == "start" else "")
                    + f"time.sleep({a.delay!r})\n"
                    + "native.key(0x20, control=True, alt=True)\n"
                    "print(os.getpid())\n"
                )
                injected = subprocess.run(
                    [sys.executable, "-c", program],
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    timeout=15,
                )
                assert injected.returncode == 0, injected.stderr
                report["injector_pid"] = int(injected.stdout.strip())
                assert report["injector_pid"] != os.getpid()
            record("activate.after")
            search = native.wait_for(
                lambda: next(
                    (
                        row
                        for row in native.windows(os.getpid())
                        if row["title"] == "Docking Search"
                    ),
                    None,
                ),
                "Search did not map",
            )
            report["search"] = search
            report["samples"] = []
            for _ in range(50):
                record("sample")
                report["samples"].append(int(user.GetForegroundWindow() or 0))
                time.sleep(0.03)
            report["focused"] = report["samples"][-1] == search["hwnd"]
            assert report["focused"], "Search did not acquire native foreground focus"
            if report["focused"]:
                native.type_text("clock")
                native.wait_for(
                    lambda: (
                        call(lambda: shared["ui"].search.window.search_entry.get_text())
                        == "clock"
                    ),
                    "Typing focus was missing",
                )
                report["typing"] = True
                native.wait_for(
                    lambda: call(lambda: shared["ui"].search._current_query) == "clock",
                    "Search did not process the typed query",
                )
                native.wait_for(
                    lambda: (
                        not call(
                            lambda: (
                                shared["ui"]
                                .search._coordinator.snapshot()
                                .pending_provider_ids
                            )
                        )
                    ),
                    "Search providers did not settle",
                )
            ImageGrab.grab(all_screens=True).save(a.output / "desktop.png")
            native.click(1100, 100)
            time.sleep(0.8)
            assert user.GetForegroundWindow() != search["hwnd"], (
                "Search reclaimed focus after an outside click"
            )
            report["outside_click_keeps_focus"] = True
        except Exception as exc:
            errors.append(exc)
            report["error"] = repr(exc)
        finally:
            if "ui" in shared:
                call(lambda: shared["ui"].window.close())

    driver = threading.Thread(target=drive, daemon=True)
    driver.start()
    try:
        app.main()
        driver.join(10)
        assert not driver.is_alive()
        assert native.workarea() == baseline, (
            "Normal shutdown did not restore the work area"
        )
        from docking.platform.backends.windows.hotkeys import (
            MOD_ALT,
            MOD_CONTROL,
            WindowsHotkeyAPI,
        )

        binding = WindowsHotkeyAPI()
        binding.register(MOD_CONTROL | MOD_ALT, 0x20)
        binding.unregister()
        report["normal_shutdown"] = True
    finally:
        user.UnhookWinEvent(hook)
        report["events"] = events
        (a.output / "result.json").write_text(json.dumps(report, indent=2))
        print(
            json.dumps(
                {
                    key: value
                    for key, value in report.items()
                    if key not in ("events", "samples")
                },
                indent=2,
            )
        )
    if errors:
        raise errors[0]


if __name__ == "__main__":
    main()
