"""Real Windows keyboard-layout/menu acceptance with owned Notepad input.

Temporarily loads French and US Dvorak layouts, verifies physical scan-code
translation in a saved document and restores the original input-locale set.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

_ORACLE = r"""
using System;
using System.Runtime.InteropServices;
public static class InputOracle {
 [StructLayout(LayoutKind.Sequential)] public struct Rect {public int l,t,r,b;}
 [StructLayout(LayoutKind.Sequential)] public struct Gui {
  public uint size,flags;
  public IntPtr active,focus,capture,menu,move,caret;
  public Rect rect;
 }
 [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(
  IntPtr hwnd, out uint pid);
 [DllImport("user32.dll")] static extern IntPtr GetKeyboardLayout(uint tid);
 [DllImport("user32.dll")] static extern bool GetGUIThreadInfo(uint tid,ref Gui g);
 [DllImport("user32.dll")] static extern bool SystemParametersInfo(
  uint action,uint param,out IntPtr value,uint flags);
 public static string Code(IntPtr h) {
  return unchecked((uint)h.ToInt64()).ToString("x8");
 }
 public static string Foreground() {
  uint pid; uint tid=GetWindowThreadProcessId(GetForegroundWindow(),out pid);
  Gui g=new Gui();g.size=(uint)Marshal.SizeOf(g);
  if(GetGUIThreadInfo(tid,ref g) && g.focus!=IntPtr.Zero)
   tid=GetWindowThreadProcessId(g.focus,out pid);
  return Code(GetKeyboardLayout(tid));
 }
 public static string Default() {
  IntPtr h;
  if(!SystemParametersInfo(0x59,0,out h,0))throw new Exception("No default locale");
  return Code(h);
 }
 public static long WheelRoute() {
  IntPtr v;
  if(!SystemParametersInfo(0x201c,0,out v,0))return -1;
  return v.ToInt64();
 }
}
"""


def oracle():
    script = (
        "$ErrorActionPreference='Stop';"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);"
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -TypeDefinition @'\n"
        + _ORACLE
        + "\n'@;\n"
        + r"""
$rows=@([Windows.Forms.InputLanguage]::InstalledInputLanguages | ForEach-Object {
 [ordered]@{code=[InputOracle]::Code($_.Handle);name=$_.LayoutName;
  language=$_.Culture.DisplayName}
});
function RegValues($path) {
 $values=[ordered]@{};
 if(Test-Path -LiteralPath $path){
  $key=Get-Item -LiteralPath $path;
  foreach($name in $key.GetValueNames()){$values[$name]=$key.GetValue($name)}
 };return $values
};
[ordered]@{loaded=$rows;active=[InputOracle]::Foreground();wheel_route=[InputOracle]::WheelRoute();
 default=[InputOracle]::Default();
 preload=(RegValues 'HKCU:\Keyboard Layout\Preload');
 substitutes=(RegValues 'HKCU:\Keyboard Layout\Substitutes')} |
 ConvertTo-Json -Depth 5 -Compress
"""
    )
    result = subprocess.run(
        [
            str(
                Path(os.environ["SYSTEMROOT"])
                / "System32/WindowsPowerShell/v1.0/powershell.exe"
            ),
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            base64.b64encode(script.encode("utf-16-le")).decode(),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python in an owned test VM")
    output = args.output.resolve()
    if (output / "result.json").exists():
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(output / "config")
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.applets.keyboardlayout.windows import (
        WindowsKeyboardAPI,
        WindowsKeyboardBackend,
        _code,
    )
    from docking.platform import targets
    from docking.platform.backends.windows.ffi import bind
    from tools.windows.visual.desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    assert not any(
        w["title"] == "Docking"
        or "Notepad" in w["title"]
        or "On-Screen Keyboard" in w["title"]
        or w["title"] == "Settings"
        for w in native.windows()
    )
    api = WindowsKeyboardAPI()
    before = oracle()
    loaded_before = api.layouts()
    workarea = native.workarea()
    original_thread = api.active(0)
    user = ctypes.WinDLL("user32", use_last_error=True)
    load = bind(
        user, "LoadKeyboardLayoutW", ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32
    )
    unload = bind(user, "UnloadKeyboardLayout", ctypes.c_int32, ctypes.c_void_p)
    activate = bind(
        user,
        "ActivateKeyboardLayout",
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
    )
    report = {"baseline": before, "owned_layouts": [], "states": {}, "characters": {}}
    from docking.applets.keyboardlayout.applet import KeyboardLayoutApplet
    from docking.platform.backends.windows.menus import WindowsMenuFocus

    original_scroll = KeyboardLayoutApplet.on_scroll

    def trace_scroll(applet, direction_up):
        entry = {"up": direction_up, "before": applet._layout._asdict()}
        original_scroll(applet, direction_up)
        entry["after"] = applet._layout._asdict()
        report.setdefault("input_events", []).append(entry)

    KeyboardLayoutApplet.on_scroll = trace_scroll

    original_restore = WindowsMenuFocus.restore

    def trace_restore(focus, previous, parent):
        entry = {
            "previous": previous,
            "parent": parent,
            "foreground": focus.foreground(),
            "previous_identity": focus.identity(previous[0]) if previous else None,
        }
        raw = focus._set_foreground

        def request(hwnd):
            value = raw(hwnd)
            entry["native_result"] = bool(value)
            return value

        focus._set_foreground = request
        try:
            result = original_restore(focus, previous, parent)
        finally:
            focus._set_foreground = raw
        entry["after"] = focus.foreground()
        report.setdefault("focus_restoration", []).append(entry)
        return result

    WindowsMenuFocus.restore = trace_restore
    errors, shared, owned = [], {}, []
    ready = threading.Event()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://keyboardlayout"},
                    {"kind": "applet", "target": "applet://clock"},
                ],
                "icon_size": 64,
                "hide_mode": "none",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    def publish():
        temporary = output / "result.tmp"
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(output / "result.json")

    original = app.build_dock_window

    def build(**kwargs):
        ui = original(**kwargs)
        shared.update(ui=ui, model=kwargs["model"])
        ready.set()
        return ui

    app.build_dock_window = build

    def call(callback):
        done, values, failures = threading.Event(), [], []

        def invoke():
            try:
                values.append(callback())
            except Exception as exc:
                failures.append(exc)
            finally:
                done.set()
            return False

        GLib.idle_add(invoke)
        assert done.wait(20), "GTK did not service an observation"
        if failures:
            raise failures[0]
        return values[0]

    # The driver is defined below so all input, restoration and errors are
    # captured by the same durable result and the normal app shutdown path.
    def drive():
        notepad = None
        owned_windows = []
        try:
            assert ready.wait(90)
            applet = call(lambda: shared["model"].get_applet("applet://keyboardlayout"))
            shared["applet"] = applet
            assert isinstance(applet._backend, WindowsKeyboardBackend)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock did not map",
            )
            wanted = {"us": "win32:04090409"}
            assert any(_code(handle) == wanted["us"] for handle in loaded_before), (
                "Verifier requires US baseline"
            )
            for name, klid in (("fr", "0000040c"), ("dvorak", "00010409")):
                handle = load(klid, 2)
                assert handle, "Failed to load owned keyboard layout: " + klid
                wanted[name] = _code(handle)
                if handle not in loaded_before:
                    owned.append(handle)
                report["owned_layouts"] = [_code(h) for h in owned]
                publish()
            document = output / "docking-keyboard-native-acceptance.txt"
            document.write_text("", encoding="utf-8")
            assert targets.open_target(str(document)), "Owned document did not open"
            notepad = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows()
                        if document.name in w["title"] and "Notepad" in w["title"]
                    ),
                    None,
                ),
                "Owned Notepad did not map",
                30,
            )

            def focus_editor():
                window = next(
                    w for w in native.windows() if w["hwnd"] == notepad["hwnd"]
                )
                left, top, right, bottom = window["rect"]
                native.click(
                    left + min(150, (right - left) / 2),
                    top + min(200, (bottom - top) * 0.6),
                )
                native.wait_for(
                    lambda: (
                        api.foreground() is not None
                        and api.foreground().root == notepad["hwnd"]
                    ),
                    "Notepad did not receive foreground",
                )

            focus_editor()
            editor_context = api.foreground()
            editor_original = api.active(editor_context.thread)
            shared.update(
                editor_context=editor_context, editor_original=editor_original
            )

            def point():
                frame = shared["ui"].window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://keyboardlayout"
                )
                window = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                return window["rect"][0] + rect.x + rect.w / 2, window["rect"][
                    1
                ] + rect.y + rect.h / 2

            def wait_layout(code):
                native.wait_for(
                    lambda: call(
                        lambda: (
                            not applet._set_in_progress
                            and applet._active == code
                            and len(applet._layout.details) == len(applet._available)
                        )
                    ),
                    "Layout did not reach UI: " + code,
                    20,
                )

            def snapshot(name):
                observed = oracle()
                state = call(lambda: applet._layout)
                assert state.active.split(":")[-1] == observed["active"]
                report["states"][name] = {
                    "state": state._asdict(),
                    "tooltip": call(lambda: applet.item.name),
                    "os": observed,
                }
                native.user.SetCursorPos(*map(int, call(point)))
                time.sleep(1.1)
                ImageGrab.grab(all_screens=True).save(output / (name + ".png"))
                publish()

            def menu():
                native.click(*call(point), right=True)
                root = native.wait_for(
                    lambda: call(lambda: Gtk.grab_get_current()),
                    "Keyboard menu did not map",
                )
                assert isinstance(root, Gtk.Menu)
                return call(
                    lambda: [
                        {
                            "label": row.get_label(),
                            "active": row.get_active()
                            if isinstance(row, Gtk.RadioMenuItem)
                            else None,
                        }
                        for row in root.get_children()
                        if row.get_label() and row.get_sensitive()
                    ]
                )

            def choose_label(label, rows):
                index = next(
                    index for index, row in enumerate(rows) if row["label"] == label
                )
                native.key(0x24)
                for _ in range(index):
                    native.key(0x28)
                native.key(0x0D)

            def select(code, name):
                rows = menu()
                info = next(
                    info
                    for info in call(lambda: applet._layout.details)
                    if info.code == code
                )
                assert sum(row["active"] is True for row in rows) == 1
                report.setdefault("menus", {})[name] = rows
                ImageGrab.grab(all_screens=True).save(output / (name + "-menu.png"))
                choose_label(f"{info.label} - {info.name}", rows)
                wait_layout(code)
                native.wait_for(
                    lambda: (
                        api.foreground() is not None
                        and api.foreground().root == notepad["hwnd"]
                    ),
                    "Source menu did not restore Notepad foreground",
                    5,
                )

            def physical_character(name, expected):
                focus_editor()
                native.key(0x41, control=True)
                native.key(0x08)
                native.scan(0x10)  # US Q key position, not a Unicode/VK character.
                native.key(0x53, control=True)
                native.wait_for(
                    lambda: document.read_text(encoding="utf-8-sig") == expected,
                    "Notepad physical character mismatch: " + name,
                    20,
                )
                report["characters"][name] = document.read_text(encoding="utf-8-sig")
                publish()

            def aim_keyboard():
                x, y = call(point)
                native.user.SetCursorPos(int(x), int(y))

                def hovered():
                    item = shared["ui"].window.hover.hovered_item
                    return (
                        item is not None
                        and item.desktop_id == "applet://keyboardlayout"
                    )

                native.wait_for(
                    lambda: native.window_at(x, y) == dock["hwnd"] and call(hovered),
                    "Pointer did not enter the keyboard applet's native input region",
                )

            wait_layout(wanted["us"])
            physical_character("us", "q")
            snapshot("native-us")
            for name, expected in (("fr", "a"), ("dvorak", "'"), ("us", "q")):
                select(wanted[name], "select-" + name)
                physical_character(name, expected)
                snapshot("native-" + name)
            # Mouse click and both wheel directions use the real current list.
            native.click(*call(point))
            wait_layout(wanted["fr"])
            physical_character("click-fr", "a")
            aim_keyboard()
            native.scroll(120)
            wait_layout(wanted["dvorak"])
            physical_character("scroll-dvorak", "'")
            aim_keyboard()
            native.scroll(-120)
            wait_layout(wanted["fr"])
            snapshot("native-scroll-fr")

            # An actual Windows change, outside the applet, must reach polling.
            focus_editor()
            assert api.request(
                api.foreground(),
                next(h for h in loaded_before if _code(h) == wanted["us"]),
            )
            wait_layout(wanted["us"])
            snapshot("native-external-us")
            select(wanted["fr"], "viewer-fr")
            rows = menu()
            choose_label("Show Current Layout", rows)
            viewer = native.wait_for(
                lambda: next(
                    (w for w in native.windows() if "On-Screen Keyboard" in w["title"]),
                    None,
                ),
                "Native On-Screen Keyboard did not map",
                30,
            )
            owned_windows.append(viewer["hwnd"])
            time.sleep(1)
            ImageGrab.grab(all_screens=True).save(output / "native-layout-viewer.png")
            report["viewer"] = viewer
            native.user.PostMessageW(viewer["hwnd"], 0x10, 0, 0)
            native.wait_for(
                lambda: not any(w["hwnd"] == viewer["hwnd"] for w in native.windows()),
                "Owned OSK did not close",
            )
            focus_editor()
            rows = menu()
            choose_label("Keyboard Settings", rows)
            settings = native.wait_for(
                lambda: next(
                    (w for w in native.windows() if w["title"] == "Settings"), None
                ),
                "Native keyboard Settings did not map",
                30,
            )
            owned_windows.append(settings["hwnd"])
            time.sleep(2)
            ImageGrab.grab(all_screens=True).save(
                output / "native-keyboard-settings.png"
            )
            report["settings"] = settings
            native.user.PostMessageW(settings["hwnd"], 0x10, 0, 0)
            native.wait_for(
                lambda: (
                    not any(w["hwnd"] == settings["hwnd"] for w in native.windows())
                ),
                "Owned Settings did not close",
            )
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            if "applet" in shared:

                def diagnostic():
                    backend = shared["applet"]._backend
                    target = backend._target
                    foreground = api.foreground()
                    return {
                        "state": shared["applet"]._layout._asdict(),
                        "target": target.__dict__ if target else None,
                        "valid": api.valid(target) if target else False,
                        "foreground": foreground.__dict__ if foreground else None,
                        "input_desktop_active": api.input_desktop_active(),
                        "target_layout": _code(api.active(target.thread))
                        if target
                        else None,
                    }

                report["diagnostic"] = call(diagnostic)
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            for hwnd in owned_windows:
                if native.user.IsWindow(hwnd):
                    native.user.PostMessageW(hwnd, 0x10, 0, 0)
            if notepad is not None:
                try:
                    focus_editor()
                    context = api.foreground()
                    assert api.request(context, shared["editor_original"])
                    native.wait_for(
                        lambda: api.active(context.thread) == shared["editor_original"],
                        "Notepad input layout did not restore",
                    )
                    native.key(0x53, control=True)
                    # Close only this verifier's saved tabs, including its
                    # previous owned runs restored by Notepad's session history.
                    for _ in range(20):
                        current = next(
                            (
                                w
                                for w in native.windows()
                                if w["hwnd"] == notepad["hwnd"]
                            ),
                            None,
                        )
                        if (
                            current is None
                            or "docking-keyboard-native-acceptance.txt"
                            not in current["title"]
                        ):
                            break
                        native.key(0x57, control=True)
                    native.user.PostMessageW(notepad["hwnd"], 0x10, 0, 0)
                    native.wait_for(
                        lambda: (
                            not any(
                                w["hwnd"] == notepad["hwnd"] for w in native.windows()
                            )
                        ),
                        "Owned Notepad did not close",
                        20,
                    )
                    report["notepad_closed"] = True
                except Exception as exc:
                    errors.append(exc)
                    report.update(ok=False, notepad_cleanup_error=repr(exc))
            if ready.is_set():

                def close():
                    grab = Gtk.grab_get_current()
                    if isinstance(grab, Gtk.MenuShell):
                        grab.cancel()
                    shared["ui"].window.close()

                try:
                    call(close)
                except Exception as exc:
                    errors.append(exc)
                    report.update(ok=False, close_error=repr(exc))
            publish()

    driver = threading.Thread(
        target=drive, name="windows-keyboardlayout-smoke", daemon=False
    )
    driver.start()
    try:
        app.main()
        driver.join(90)
        assert not driver.is_alive()
        assert native.workarea() == workarea
        if errors:
            raise errors[0]
    finally:
        # Restore only this test process's locale before releasing its owned
        # runtime handles. Never rewrite preferred language/keyboard settings.
        activate(original_thread, 0x100)
        released = [bool(unload(handle)) for handle in reversed(owned)]
        report["owned_layouts_released"] = all(released)
        report["loaded_set_restored"] = set(api.layouts()) == set(loaded_before)
        restored = oracle()
        report["restored"] = restored
        report["preferences_restored"] = all(
            restored[k] == before[k] for k in ("default", "preload", "substitutes")
        )
        report["normal_shutdown"] = (
            shared.get("applet") is not None
            and shared["applet"]._stopped
            and shared["applet"]._timer_id == 0
        )
        publish()
        print(json.dumps(report, indent=2))
        assert (
            report["owned_layouts_released"]
            and report["loaded_set_restored"]
            and report["preferences_restored"]
        )


if __name__ == "__main__":
    main()
