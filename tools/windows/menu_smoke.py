"""Exercise menu keyboard input, focus and normal shutdown on Windows.

Run with --shutdown keyboard and --shutdown native in separate fresh jobs.
Both use the full application, native SendInput and independently observed
HWNDs. GTK state is observed only on the main loop; actions use real input.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shutdown", choices=("keyboard", "native"), required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(output / "config")
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.platform.backends.windows.hotkeys import (
        MOD_ALT,
        MOD_CONTROL,
        WindowsHotkeyAPI,
    )
    from docking.platform.backends.windows.menus import WindowsMenuFocus
    from docking.ui.menu import MenuHandler
    from tools.windows.desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    assert not any(
        w["title"] == "Docking" or w["class"] == "Notepad" for w in native.windows()
    ), "Close an existing dock or Notepad before starting the owned fixture"
    baseline = native.workarea()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [{"kind": "applet", "target": "applet://clock"}],
                "icon_size": 48,
                "hide_mode": "none",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
                "global_search_enabled": True,
                "global_search_shortcut": "CTRL+ALT+space",
            }
        ),
        encoding="utf-8",
    )
    shared, report, errors = {}, {"baseline_workarea": baseline}, []
    ready = threading.Event()
    original_build = app.build_dock_window
    original_menu = MenuHandler._new_popup_menu
    original_restore = WindowsMenuFocus.restore

    def restore_focus(focus, previous, parent):
        attempt = {"previous": previous, "parent": parent, "before": focus.foreground()}
        original_restore(focus, previous, parent)
        attempt["after"] = focus.foreground()
        report.setdefault("focus_restoration", []).append(attempt)

    WindowsMenuFocus.restore = restore_focus

    def build(**kwargs):
        ui = original_build(**kwargs)
        shared.update(ui=ui, kwargs=kwargs)
        ready.set()
        return ui

    def new_menu(handler):
        report.setdefault("menu_creation_foregrounds", []).append(
            native.user.GetForegroundWindow()
        )
        menu = original_menu(handler)
        shared["menu"] = menu

        def watch(root, depth=0):
            root.connect(
                "key-press-event",
                lambda widget, event: (
                    report.setdefault("key_events", []).append(
                        {"depth": depth, "key": event.keyval}
                    )
                    or False
                ),
            )
            for child in root.get_children():
                submenu = child.get_submenu()
                if submenu:
                    watch(submenu, depth + 1)

        menu.connect("map", lambda root: watch(root))
        return menu

    app.build_dock_window = build
    MenuHandler._new_popup_menu = new_menu

    def ui_call(callback):
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
        assert done.wait(15), "GTK did not service a queued observation"
        if failures:
            raise failures[0]
        return values[0]

    def state():
        def label(item):
            if item is None:
                return None
            if item.get_label():
                return item.get_label()
            child = item.get_child()
            if isinstance(child, Gtk.Container):
                return next(
                    (
                        w.get_text()
                        for w in child.get_children()
                        if isinstance(w, Gtk.Label)
                    ),
                    None,
                )
            return None

        root = shared.get("menu")
        chain = []
        menu = root
        while menu and menu.get_mapped():
            selected = menu.get_selected_item()
            chain.append(label(selected))
            menu = selected.get_submenu() if selected else None
        return {
            "visible": bool(root and root.get_visible()),
            "selected": chain,
            "grab": Gtk.grab_get_current() is not None,
            "accept_focus": shared["ui"].window.get_accept_focus(),
        }

    def capture(name):
        ImageGrab.grab(all_screens=True).save(output / (name + ".png"))

    def drive():
        editor, dock = None, None
        try:
            assert ready.wait(90), "The complete dock did not start"
            kwargs = shared["kwargs"]
            app_id = ui_call(
                lambda: next(
                    a.desktop_id
                    for a in kwargs["application_registry"].snapshot()
                    if a.name.lower() == "notepad"
                )
            )
            assert ui_call(lambda: kwargs["application_launcher"].launch(app_id))
            editor = native.wait_for(
                lambda: next(
                    (w for w in native.windows() if w["class"] == "Notepad"), None
                ),
                "Owned Notepad did not appear",
                40,
            )
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Docking did not appear",
            )
            time.sleep(1)

            def foreground_editor():
                left, top, right, bottom = editor["rect"]
                native.click((left + right) // 2, (top + bottom) // 2)
                native.wait_for(
                    lambda: native.user.GetForegroundWindow() == editor["hwnd"],
                    "Owned editor did not gain foreground",
                )

            def open_menu():
                row = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                left, _top, right, bottom = row["rect"]
                native.click((left + right) // 2, bottom - 25, right=True, control=True)
                native.wait_for(
                    lambda: ui_call(state)["visible"], "Root menu did not open"
                )
                assert native.user.GetForegroundWindow() == dock["hwnd"]
                assert ui_call(state)["accept_focus"]

            def closed():
                value = ui_call(state)
                return (
                    not value["visible"]
                    and not value["grab"]
                    and not value["accept_focus"]
                )

            foreground_editor()
            open_menu()
            native.key(0x28)  # Down
            assert ui_call(state)["selected"] == ["Search..."]
            native.key(0x28)
            assert ui_call(state)["selected"] == ["Add Applet"]
            native.key(0x27)  # Right
            native.key(0x27)
            native.wait_for(
                lambda: len(ui_call(state)["selected"]) == 3,
                "Nested submenu did not open",
            )
            native.key(0x28)
            report["nested_menu"] = ui_call(state)
            report["nested_foreground"] = native.user.GetForegroundWindow()
            capture("nested-keyboard-menu")
            native.key(0x25)  # Left
            report["after_left"] = ui_call(state)
            report["after_left_foreground"] = native.user.GetForegroundWindow()
            # GTK leaves the previous submenu displayed after deselecting it.
            # Down must now move in its parent, rather than in that submenu.
            native.key(0x28)
            native.wait_for(
                lambda: ui_call(state)["selected"][1] == "Time & Productivity",
                "Left did not return to the parent submenu",
            )
            native.key(0x1B)
            native.wait_for(
                closed, "Escape did not release the menu grab and focusability"
            )
            report["foreground_after_escape"] = native.user.GetForegroundWindow()
            native.wait_for(
                lambda: native.user.GetForegroundWindow() == editor["hwnd"],
                "Escape did not restore the previous application focus",
            )
            report["escape_restores_editor_focus"] = True
            report["editor"] = editor
            report["dock"] = dock
            report["escape_released_menu"] = True

            # Native shortcut cancels an active menu before Search takes focus.
            foreground_editor()
            open_menu()
            native.key(0x28)
            native.key(0x28)
            native.key(0x27)
            native.wait_for(
                lambda: len(ui_call(state)["selected"]) >= 2,
                "Shortcut fixture did not open a submenu",
            )
            native.key(0x20, control=True, alt=True)
            search = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows(os.getpid())
                        if w["title"] == "Docking Search"
                    ),
                    None,
                ),
                "Search did not open over a menu",
            )
            native.wait_for(
                lambda: native.user.GetForegroundWindow() == search["hwnd"],
                "Search did not gain focus",
            )
            native.wait_for(closed, "Search retained the menu grab")
            native.type_text("clock")
            native.wait_for(
                lambda: (
                    ui_call(lambda: shared["ui"].search.window.search_entry.get_text())
                    == "clock"
                ),
                "Search did not receive typing",
            )
            capture("search-from-menu")
            native.key(0x1B)
            native.wait_for(
                lambda: not any(w["hwnd"] == search["hwnd"] for w in native.windows()),
                "Escape did not hide Search",
            )
            report["hotkey_from_menu_typing"] = True

            # Mouse menu actions retain their usual activation and typing focus.
            foreground_editor()
            open_menu()

            def search_row_point():
                menu = shared["menu"]
                row = menu.get_children()[0]
                x, y = menu.get_window().get_origin()[-2:]
                bounds = row.get_allocation()
                return (
                    x + bounds.x + bounds.width // 2,
                    y + bounds.y + bounds.height // 2,
                )

            native.click(*ui_call(search_row_point))
            search = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows(os.getpid())
                        if w["title"] == "Docking Search"
                    ),
                    None,
                ),
                "Mouse menu action did not open Search",
            )
            native.wait_for(closed, "Mouse action retained the menu grab")
            native.wait_for(
                lambda: native.user.GetForegroundWindow() == search["hwnd"],
                "Mouse action did not focus Search",
            )
            native.type_text("clock")
            native.wait_for(
                lambda: (
                    ui_call(lambda: shared["ui"].search.window.search_entry.get_text())
                    == "clock"
                ),
                "Mouse menu Search did not receive typing",
            )
            native.key(0x1B)
            native.wait_for(
                lambda: not any(w["hwnd"] == search["hwnd"] for w in native.windows()),
                "Mouse menu Search did not hide",
            )
            report["mouse_search_typing"] = True

            # Enter invokes the selected menu action; a new dialog keeps focus.
            foreground_editor()
            open_menu()
            native.key(0x23)  # End selects Quit
            for _ in range(3):
                native.key(0x26)  # Get Support, About, Preferences
            assert ui_call(state)["selected"] == ["Preferences"]
            native.key(0x0D)
            prefs = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows(os.getpid())
                        if "Preferences" in w["title"]
                    ),
                    None,
                ),
                "Keyboard action did not open Preferences",
            )
            native.wait_for(closed, "Preferences retained a menu grab")
            native.wait_for(
                lambda: native.user.GetForegroundWindow() == prefs["hwnd"],
                "Menu cleanup stole focus from Preferences",
            )
            report["keyboard_preferences_focus"] = True
            native.user.PostMessageW(prefs["hwnd"], 0x10, 0, 0)
            native.wait_for(
                lambda: not any(w["hwnd"] == prefs["hwnd"] for w in native.windows()),
                "Preferences did not close",
            )

            for _ in range(3):
                foreground_editor()
                open_menu()
                native.key(0x1B)
                native.wait_for(
                    closed, "Repeated dismissal left a grab or focusable dock"
                )
                native.wait_for(
                    lambda: native.user.GetForegroundWindow() == editor["hwnd"],
                    "Repeated dismissal did not restore application focus",
                )
            report["repeated_cycles"] = 3
            foreground_editor()
            open_menu()
            foreground_editor()
            native.wait_for(closed, "Outside click did not dismiss menu")
            assert native.user.GetForegroundWindow() == editor["hwnd"]
            report["outside_click_keeps_target_focus"] = True

            row = next(
                w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
            )
            native.user.SetCursorPos(
                (row["rect"][0] + row["rect"][2]) // 2, row["rect"][3] - 25
            )
            time.sleep(0.5)
            assert native.user.GetForegroundWindow() == editor["hwnd"]
            report["hover_keeps_editor_foreground"] = True

            foreground_editor()
            open_menu()
            if args.shutdown == "keyboard":
                native.key(0x23)
                assert ui_call(state)["selected"] == ["Quit"]
                capture("keyboard-quit-selected")
                native.key(0x0D)
            else:
                native.key(0x28)
                capture("native-close-menu-open")
                assert native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            native.wait_for(
                lambda: not native.user.IsWindow(dock["hwnd"]),
                "Normal close left the dock alive",
            )
            report["shutdown"] = args.shutdown
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            capture("failure")
            if dock and native.user.IsWindow(dock["hwnd"]):
                ui_call(lambda: shared.get("menu") and shared["menu"].popdown())
                ui_call(lambda: shared["ui"].window.close())
        finally:
            if editor:
                for row in native.windows(editor["pid"]):
                    native.user.PostMessageW(row["hwnd"], 0x10, 0, 0)

    driver = threading.Thread(target=drive, name="windows-menu-smoke", daemon=True)
    driver.start()
    try:
        app.main()
        driver.join(10)
        assert not driver.is_alive(), "Desktop driver survived normal shutdown"
        native.wait_for(
            lambda: native.workarea() == baseline,
            "Shutdown did not restore the work area",
        )
        report["restored_workarea"] = native.workarea()
        hotkey = WindowsHotkeyAPI()
        hotkey.register(MOD_CONTROL | MOD_ALT, 0x20)
        hotkey.unregister()
        report["hotkey_released"] = True
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
