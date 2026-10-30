"""Real Windows keyboard-layout/menu acceptance with owned Notepad input.

Temporarily loads French and US Dvorak layouts, verifies physical scan-code
translation in a saved document and restores the original input-locale set.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path

from tools.windows.visual.input_oracle import oracle


def main(monitor):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--owned-amd64-editor",
        action="store_true",
        help="Use an explicitly AMD64 Forms editor instead of stock Notepad",
    )
    parser.add_argument(
        "--search-input",
        action="store_true",
        help="Check Search typing, dead keys and shortcut recovery across layouts",
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires native Windows Python in an owned test VM")
    output = args.output.resolve()
    if (output / "result.json").exists():
        parser.error("Use a fresh output directory")
    output.mkdir(parents=True, exist_ok=True)
    monitor["output_directory"] = str(output)
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
    from docking.platform.backends.windows.hotkeys import (
        MOD_ALT,
        MOD_CONTROL,
        WindowsHotkeyAPI,
    )
    from docking.platform.backends.windows.input_service import WindowsInputService
    from docking.search.services.global_shortcuts import GlobalShortcutsState
    from tools.windows.visual.desktop_driver import WindowsDesktop, write_shared_json
    from tools.windows.visual.input_editor import OwnedInputEditor
    from tools.windows.visual.input_editor import focus_editor as click_editor
    from tools.windows.visual.input_environment import (
        runtime_snapshot,
        target_modules,
        thread_snapshot,
    )
    from tools.windows.visual.input_fixture import ime_conversion
    from tools.windows.visual.input_gtk import snapshot as gtk_input_snapshot
    from tools.windows.visual.input_requests import (
        CleanupJournal,
        request_controls,
        trace_requests,
    )

    native = WindowsDesktop()
    assert not any(
        w["title"] == "Docking"
        or "Notepad" in w["title"]
        or "On-Screen Keyboard" in w["title"]
        or w["title"] == "Settings"
        for w in native.windows()
    )
    api = WindowsKeyboardAPI()
    before = oracle(preferences_only=True)
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
    report = {
        "baseline": before,
        "owned_layouts": [],
        "states": {},
        "characters": {},
        "text_services": monitor,
        "text_services_report": "text-services.json",
        "editor_mode": "owned-amd64" if args.owned_amd64_editor else "stock-notepad",
    }
    from docking.applets.keyboardlayout.applet import KeyboardLayoutApplet
    from docking.platform.backends.windows.menus import WindowsMenuFocus

    original_select = KeyboardLayoutApplet._select_layout

    def trace_selection(applet, code, *, context=None):
        entry = {"code": code, "context": context, "before": applet._layout._asdict()}
        report.setdefault("selection_callbacks", []).append(entry)
        try:
            return original_select(applet, code, context=context)
        finally:
            entry["after"] = applet._layout._asdict()

    KeyboardLayoutApplet._select_layout = trace_selection

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
                "global_search_enabled": True,
                "global_search_shortcut": "CTRL+ALT+space",
            }
        ),
        encoding="utf-8",
    )

    def publish():
        write_shared_json(output / "result.json", report)

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
        owned_editor = None
        owned_windows = []
        try:
            assert ready.wait(90)
            report["input_runtime"] = runtime_snapshot()
            publish()
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
            request_controls(
                native,
                output,
                french=int(wanted["fr"].split(":")[1], 16),
                journal=report.setdefault("direct_requests", {}),
                publish=publish,
            )
            document = output / "docking-keyboard-native-acceptance.txt"
            document.write_text("", encoding="utf-8")
            if args.owned_amd64_editor:
                owned_editor = OwnedInputEditor(
                    native,
                    output / "editor-64",
                    document,
                    bits=64,
                    machine=WindowsInputService()._thread._machine,
                )
                report["owned_editor"] = owned_editor.journal
                publish()
                notepad = owned_editor.start()
            else:
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
                click_editor(native, notepad)
                native.wait_for(
                    lambda: (
                        api.foreground() is not None
                        and api.foreground().root == notepad["hwnd"]
                    ),
                    "Notepad did not receive foreground",
                )

            focus_editor()
            editor_context = api.foreground()
            assert editor_context is not None
            machine = WindowsInputService()._thread._machine(editor_context.pid)
            assert machine == 0x8664, "Reference typing window must be an AMD64 process"
            report["editor_machine"] = f"0x{machine:04x}"
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
                action = {"requested_label": label, "requested_index": index}
                report.setdefault("menu_actions", []).append(action)
                native.key(0x24)
                for _ in range(index):
                    native.key(0x28)

                def selected():
                    menu = Gtk.grab_get_current()
                    if not isinstance(menu, Gtk.Menu):
                        return None
                    row = menu.get_selected_item()
                    return row.get_label() if row else None

                action["selected_before_enter"] = call(selected)
                publish()
                native.key(0x0D)
                action["enter_sent"] = True
                publish()

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

            def search_window():
                return next(
                    (
                        w
                        for w in native.windows(os.getpid())
                        if w["title"] == "Docking Search"
                    ),
                    None,
                )

            def open_search(key, *, shift=False):
                native.key(key, control=True, alt=True, shift=shift)
                window = native.wait_for(search_window, "Shortcut did not open Search")
                native.wait_for(
                    lambda: native.user.GetForegroundWindow() == window["hwnd"],
                    "Search did not gain foreground",
                )

            def close_search():
                native.key(0x1B)
                native.wait_for(lambda: not search_window(), "Search did not dismiss")
                native.wait_for(
                    lambda: native.user.GetForegroundWindow() == notepad["hwnd"],
                    "Search did not restore Notepad foreground",
                )

            def search_character(name, expected, scans):
                search = shared["ui"].search
                open_search(0x20)
                observed = oracle()
                native.key(0x41, control=True)
                native.key(0x08)
                for code in scans:
                    native.scan(code)

                def text():
                    return call(lambda: search.window.search_entry.get_text())

                native.wait_for(
                    lambda: text() == expected,
                    "Search physical input mismatch: " + name,
                )
                report.setdefault("search_characters", {})[name] = {
                    "text": text(),
                    "os": observed,
                }
                ImageGrab.grab(all_screens=True).save(
                    output / ("search-" + name + ".png")
                )
                close_search()

            def verify_altgr():
                from docking.platform.keyboard import text_modifier_active

                search = shared["ui"].search
                attempt = {"stage": "binding", "capture_focus": []}
                report["altgr"] = attempt
                publish()

                def configure(shortcut):
                    search._config.global_search_shortcut = shortcut
                    search.refresh_settings()

                def bound():
                    return call(
                        lambda: (
                            search.shortcut_status is not None
                            and search.shortcut_status.state
                            is GlobalShortcutsState.BOUND
                        )
                    )

                capture_window = None
                try:
                    call(lambda: configure("CTRL+ALT+e"))
                    native.wait_for(bound, "AltGr conflict shortcut did not bind")
                    focus_editor()
                    native.key(0x41, control=True)
                    native.key(0x08)
                    native.scan(0x12, altgr=True)
                    native.key(0x53, control=True)
                    native.wait_for(
                        lambda: document.read_text(encoding="utf-8-sig") == "€",
                        "Physical AltGr was consumed by the Ctrl+Alt shortcut",
                    )
                    assert search_window() is None
                    # The same shortcut must resume after releasing physical right Alt.
                    open_search(0x45)
                    native.key(0x41, control=True)
                    native.key(0x08)
                    native.scan(0x12, altgr=True)
                    native.wait_for(
                        lambda: (
                            call(lambda: search.window.search_entry.get_text()) == "€"
                        ),
                        "Search did not preserve physical AltGr text",
                    )
                    assert search_window() is not None

                    def build_capture():
                        from docking.search.ui.shortcut_capture import (
                            ShortcutCaptureButton,
                        )

                        window = Gtk.Window(title="Docking owned shortcut capture")
                        window.set_transient_for(search.window.window)
                        window.set_keep_above(True)
                        window.set_skip_taskbar_hint(True)
                        capture = ShortcutCaptureButton()
                        window.add(capture)
                        window.set_default_size(360, 120)
                        window.show_all()
                        window.present()
                        capture.begin_capture()
                        return window, capture

                    capture_window, capture = call(build_capture)
                    mapped = native.wait_for(
                        lambda: next(
                            (
                                w
                                for w in native.windows(os.getpid())
                                if w["title"] == "Docking owned shortcut capture"
                            ),
                            None,
                        ),
                        "Shortcut capture did not map",
                    )
                    left, top, right, bottom = mapped["rect"]
                    x, y = (left + right) / 2, (top + bottom) / 2
                    expected = mapped["hwnd"]
                    async_key_state = bind(
                        user, "GetAsyncKeyState", ctypes.c_int16, ctypes.c_int32
                    )

                    def capture_probe():
                        state = call(
                            lambda: {
                                "capturing": capture.capturing,
                                "shortcut": capture.get_shortcut(),
                                "has_focus": capture.has_focus(),
                                "gtk_focus_is_capture": capture_window.get_focus()
                                == capture,
                                "queued_altgr": text_modifier_active(),
                            }
                        )
                        state.update(
                            capture_hwnd=expected,
                            foreground_hwnd=int(native.user.GetForegroundWindow() or 0),
                            hit_hwnd=int(native.window_at(x, y) or 0),
                            physical_altgr=bool(async_key_state(0xA5) & 0x8000),
                        )
                        return state

                    def capture_ready():
                        return (
                            native.user.GetForegroundWindow() == expected
                            and native.window_at(x, y) == expected
                            and call(capture.has_focus)
                        )

                    def require_capture_focus(stage):
                        try:
                            native.wait_for(
                                capture_ready,
                                "Capture fixture did not own native and GTK focus",
                            )
                        finally:
                            attempt["capture_focus"].append(
                                {
                                    "stage": stage,
                                    **capture_probe(),
                                }
                            )
                            publish()

                    attempt["stage"] = "capture focus"
                    attempt["capture_mapped"] = capture_probe()
                    publish()
                    native.wait_for(
                        lambda: native.window_at(x, y) == expected,
                        "Capture fixture was covered by another native window",
                    )
                    native.click(x, y)
                    call(capture.begin_capture)
                    require_capture_focus("before_altgr")
                    attempt["stage"] = "capture AltGr"
                    publish()
                    native.scan(0x12, altgr=True)
                    native.wait_for(
                        lambda: (
                            not async_key_state(0xA5) & 0x8000
                            and not call(text_modifier_active)
                        ),
                        "Physical AltGr release did not drain before the next chord",
                    )
                    attempt["capture_after_altgr"] = capture_probe()
                    publish()
                    assert attempt["capture_after_altgr"]["capturing"]
                    assert not attempt["capture_after_altgr"]["shortcut"]
                    require_capture_focus("before_ctrl_alt")
                    attempt["stage"] = "capture Ctrl+Alt"
                    publish()
                    native.key(0x4B, control=True, alt=True)
                    try:
                        native.wait_for(
                            lambda: call(capture.get_shortcut) == "CTRL+ALT+k",
                            "Ordinary Ctrl+Alt did not capture after AltGr",
                        )
                    finally:
                        attempt["capture_after_ctrl_alt"] = capture_probe()
                        publish()
                    attempt.update(
                        stage="complete",
                        editor=document.read_text(encoding="utf-8-sig"),
                        search=call(lambda: search.window.search_entry.get_text()),
                        capture_ignored_text=True,
                        capture_ctrl_alt=call(capture.get_shortcut),
                        shortcut_resumed=True,
                    )
                    ImageGrab.grab(all_screens=True).save(output / "native-altgr.png")
                finally:
                    cleanup = {
                        "foreground_before": int(
                            native.user.GetForegroundWindow() or 0
                        ),
                        "editor_hwnd": notepad["hwnd"],
                    }
                    attempt["cleanup"] = cleanup
                    publish()
                    if capture_window is not None:
                        call(capture_window.destroy)
                    # Destroying the transient capture reactivates its parent.
                    # Hide that keep-above palette before clicking the editor:
                    # CI's editor and Search rectangles can overlap.
                    call(search.window.hide)
                    native.wait_for(
                        lambda: (
                            not any(
                                w["title"]
                                in {"Docking Search", "Docking owned shortcut capture"}
                                for w in native.windows(os.getpid())
                            )
                        ),
                        "AltGr fixture windows did not close before editor focus",
                    )
                    cleanup["owned_overlays_closed"] = True
                    cleanup["foreground_after_close"] = int(
                        native.user.GetForegroundWindow() or 0
                    )
                    publish()
                    focus_editor()
                    cleanup["foreground_after_editor_focus"] = int(
                        native.user.GetForegroundWindow() or 0
                    )
                    publish()
                    call(lambda: configure("CTRL+ALT+space"))
                    native.wait_for(
                        bound, "Search shortcut did not restore after AltGr"
                    )
                publish()

            def verify_search_profiles():
                from tools.windows.visual.input_profile_fixture import (
                    TELEX_IDENTIFIER,
                    VIETNAMESE_CONVERSION_MODE,
                    text_profile_fixtures,
                )

                journal = {"composition": []}
                report["search_profiles"] = journal
                search = shared["ui"].search
                input_service = WindowsInputService()

                def restore_keyboard():
                    state = applet._backend.query()
                    applet._backend.switch(wanted["us"], context=state.context)
                    native.wait_for(
                        lambda: applet._backend.query().active == wanted["us"],
                        "Profile fixture did not restore the initial keyboard",
                    )

                def exercise(profile, entry, window, mapped, attempt):
                    mode_scope = ExitStack()
                    preedit = []
                    subscription = call(
                        lambda: entry.connect(
                            "preedit-changed",
                            lambda widget, text: preedit.append(text),
                        )
                    )
                    left, top, right, bottom = mapped["rect"]
                    x, y = (left + right) / 2, (top + bottom) / 2

                    def observation():
                        return call(
                            lambda: {
                                "text": entry.get_text(),
                                "preedit": tuple(preedit),
                                "visible": window.get_visible(),
                                "has_focus": entry.has_focus(),
                            }
                        )

                    def focused():
                        return (
                            native.user.GetForegroundWindow() == mapped["hwnd"]
                            and native.window_at(x, y) == mapped["hwnd"]
                            and call(entry.has_focus)
                        )

                    def observe_input(stage):
                        context = api.foreground()
                        facts = {
                            "context": asdict(context) if context else None,
                            "modules": target_modules(os.getpid()),
                            "foreground_is_fixture": bool(
                                context
                                and context.root == mapped["hwnd"]
                                and context.pid == os.getpid()
                            ),
                        }
                        try:
                            if context is not None and facts["foreground_is_fixture"]:
                                snapshot = input_service.read(
                                    context, threading.Event()
                                )
                                facts["target_snapshot"] = (
                                    asdict(snapshot) if snapshot else None
                                )
                        except OSError as error:
                            facts["target_snapshot_error"] = repr(error)
                        try:
                            facts["ui"] = call(
                                lambda: {
                                    "thread": thread_snapshot(mapped["hwnd"]),
                                    "gtk": gtk_input_snapshot(entry),
                                }
                            )
                        except (OSError, RuntimeError) as error:
                            facts["ui_observation_error"] = repr(error)
                        attempt.setdefault("input_observations", {})[stage] = facts
                        publish()

                    try:
                        native.wait_for(
                            focused, "IME fixture did not own native/GTK focus"
                        )
                        observe_input("before_selection")
                        state = applet._backend.query()
                        applet._backend.switch(profile.code, context=state.context)
                        native.wait_for(
                            lambda: applet._backend.query().active == profile.code,
                            "IME fixture did not select the requested profile GUID",
                        )
                        native.wait_for(
                            focused, "Profile activation changed fixture focus"
                        )
                        observe_input("after_activation")
                        context = api.foreground()
                        assert (
                            context is not None
                            and context.root == mapped["hwnd"]
                            and context.pid == os.getpid()
                        ), "IME setup lost the owned fixture target"

                        def validate_profile():
                            # This runs on the driver, outside GTK dispatch:
                            # reading the actual input thread needs its UI loop.
                            if api.foreground() != context:
                                raise RuntimeError("Owned IME target changed")
                            snapshot = input_service.read(context, threading.Event())
                            if (
                                snapshot is None
                                or not snapshot.ime_known
                                or snapshot.profile is None
                                or snapshot.profile.code != profile.code
                            ):
                                raise RuntimeError("Owned IME profile changed")

                        attempt["ime_mode"] = {}
                        mode_scope.enter_context(
                            ime_conversion(
                                window=context.window,
                                root=context.root,
                                pid=context.pid,
                                thread=context.thread,
                                journal=attempt["ime_mode"],
                                dispatch=call,
                                validate=validate_profile,
                                conversion=VIETNAMESE_CONVERSION_MODE,
                            )
                        )
                        observe_input("after_mode_setup")
                        attempt["profile_readback"] = applet._backend.query().active
                        attempt["foreground_hwnd"] = int(
                            native.user.GetForegroundWindow()
                        )
                        attempt["fixture_hwnd"] = mapped["hwnd"]
                        attempt["hit_hwnd"] = int(native.window_at(x, y) or 0)
                        attempt["stage"] = "compose"
                        publish()
                        call(lambda: entry.set_text(""))
                        native.scan(0x1E)
                        native.scan(
                            0x1E if profile.identifier == TELEX_IDENTIFIER else 0x07
                        )
                        observe_input("after_first_keys")
                        native.wait_for(
                            lambda: call(lambda: bool(preedit and preedit[-1])),
                            "IME fixture did not compose before Return",
                        )
                        before = observation()
                        attempt.update(
                            stage="commit",
                            text_before_commit=before["text"],
                            preedit_before_commit=before["preedit"],
                        )
                        publish()
                        native.key(0x0D)
                        try:
                            native.wait_for(
                                lambda: call(entry.get_text).strip() == "â",
                                "IME fixture did not commit composed â on Return",
                            )
                            native.wait_for(
                                lambda: call(lambda: bool(preedit) and not preedit[-1]),
                                "Return did not end fixture preedit",
                            )
                        finally:
                            after = observation()
                            attempt.update(
                                text_after_commit=after["text"],
                                visible_after_commit=after["visible"],
                                focused_after_commit=after["has_focus"],
                                preedit_after_commit=after["preedit"],
                                enter_committed_preedit=True,
                            )
                            publish()
                        assert attempt["visible_after_commit"]
                        assert after["has_focus"]
                        native.wait_for(focused, "IME commit changed fixture focus")
                        attempt["text_before_new_preedit"] = after["text"]
                        native.scan(0x1E)
                        native.wait_for(
                            lambda: call(lambda: bool(preedit and preedit[-1])),
                            "IME fixture did not start another preedit",
                        )
                        before = observation()
                        attempt.update(
                            stage="cancel",
                            text_while_preediting=before["text"],
                            preedit_before_cancel=before["preedit"],
                        )
                        publish()
                        native.key(0x1B)
                        try:
                            native.wait_for(
                                lambda: call(lambda: bool(preedit) and not preedit[-1]),
                                "Escape did not end fixture preedit",
                            )
                            # Observe settled GTK output; conversion may commit
                            # raw input instead of rolling back the prior word.
                            time.sleep(0.3)
                        finally:
                            after = observation()
                            attempt.update(
                                text_after_cancel=after["text"],
                                visible_after_cancel=after["visible"],
                                focused_after_cancel=after["has_focus"],
                                preedit_after_cancel=after["preedit"],
                            )
                            publish()
                        assert attempt["visible_after_cancel"]
                        assert after["has_focus"]
                        native.wait_for(
                            focused, "IME cancellation changed fixture focus"
                        )
                        attempt.update(
                            text=after["text"],
                            escape_cancelled_preedit=True,
                            stage="complete",
                        )
                        publish()
                    except Exception:
                        observe_input("failure")
                        raise
                    finally:
                        try:
                            mode_scope.close()
                        finally:
                            attempt["final_observation"] = observation()
                            publish()
                            call(lambda: entry.disconnect(subscription))

                def build_reference():
                    # This control has only GTK's default key/IME handlers. It
                    # shares the initialized process, never Search's handlers.
                    window = Gtk.Window(
                        title="Docking owned plain SearchEntry reference"
                    )
                    window.set_default_size(680, 160)
                    window.set_keep_above(True)
                    window.set_skip_taskbar_hint(True)
                    entry = Gtk.SearchEntry()
                    window.add(entry)
                    window.show_all()
                    window.present()
                    entry.grab_focus()
                    return window, entry

                with text_profile_fixtures(journal, publish) as profiles:
                    for profile in profiles:
                        attempt = {
                            "profile": profile.code,
                            "native_reference": (
                                "plain Gtk.SearchEntry in the same process"
                            ),
                            "reference": {},
                        }
                        journal["composition"].append(attempt)
                        publish()
                        reference_window, reference_entry = call(build_reference)
                        try:
                            mapped = native.wait_for(
                                lambda: next(
                                    (
                                        w
                                        for w in native.windows(os.getpid())
                                        if w["title"]
                                        == "Docking owned plain SearchEntry reference"
                                    ),
                                    None,
                                ),
                                "Plain SearchEntry reference did not map",
                            )
                            left, top, right, bottom = mapped["rect"]
                            native.click((left + right) / 2, (top + bottom) / 2)
                            exercise(
                                profile,
                                reference_entry,
                                reference_window,
                                mapped,
                                attempt["reference"],
                            )
                        finally:
                            try:
                                restore_keyboard()
                            finally:
                                call(reference_window.destroy)
                        focus_editor()
                        open_search(0x20)
                        try:
                            mapped = search_window()
                            assert mapped is not None
                            exercise(
                                profile,
                                search.window.search_entry,
                                search.window.window,
                                mapped,
                                attempt,
                            )
                            expected = attempt["reference"]["text_after_cancel"]
                            attempt["native_expected_after_cancel"] = expected
                            attempt["stage"] = "compare"
                            publish()
                            assert (
                                attempt["text_after_commit"]
                                == attempt["reference"]["text_after_commit"]
                            ), "Search Return differed from the default GTK reference"
                            assert attempt["text_after_cancel"] == expected, (
                                "Search Escape differed from the default GTK reference"
                            )
                            attempt["stage"] = "complete"
                            ImageGrab.grab(all_screens=True).save(
                                output / f"search-profile-{profile.identifier}.png"
                            )
                        finally:
                            restore_keyboard()
                            close_search()
                        publish()

            def refresh_dead_key_metadata():
                # A first metadata query must preserve an in-progress
                # dead key even when layouts have not yet been cached.
                observed = threading.Event()
                metadata = {}

                def refresh_metadata():
                    backend = WindowsKeyboardBackend()
                    try:
                        return backend.query()
                    finally:
                        backend.stop()

                def metadata_ready(state):
                    metadata["layouts"] = {
                        info.code: info.name for info in state.details
                    }
                    observed.set()

                call(
                    lambda: applet._worker.run(
                        name="metadata-dead-key",
                        fn=refresh_metadata,
                        on_result=metadata_ready,
                    )
                )
                assert observed.wait(20), "Metadata refresh did not complete"
                metadata["foreground"] = int(native.user.GetForegroundWindow())
                assert metadata["foreground"] == notepad["hwnd"]
                assert len(metadata["layouts"]) == len(api.layouts())
                report["dead_key_metadata"] = metadata

            def physical_character(name, expected, scans=(0x10,)):
                focus_editor()
                native.key(0x41, control=True)
                native.key(0x08)
                for index, code in enumerate(scans):
                    native.scan(code)
                    if name == "fr-dead" and index == 0:
                        refresh_dead_key_metadata()
                native.key(0x53, control=True)
                native.wait_for(
                    lambda: document.read_text(encoding="utf-8-sig") == expected,
                    "Notepad physical character mismatch: " + name,
                    20,
                )
                report["characters"][name] = document.read_text(encoding="utf-8-sig")
                if args.search_input:
                    search_character(name, expected, scans)
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
                if args.search_input and name == "fr":
                    physical_character("fr-dead", "ê", (0x1A, 0x12))
                    verify_altgr()
                snapshot("native-" + name)
            if args.search_input:
                verify_search_profiles()
                search = shared["ui"].search
                key_scan = bind(
                    user,
                    "VkKeyScanExW",
                    ctypes.c_short,
                    ctypes.c_wchar,
                    ctypes.c_void_p,
                )

                def configure(shortcut):
                    def apply():
                        search._config.global_search_shortcut = shortcut
                        search.refresh_settings()

                    call(apply)

                def wait_status(state):
                    native.wait_for(
                        lambda: call(
                            lambda: (
                                search.shortcut_status is not None
                                and search.shortcut_status.state is state
                            )
                        ),
                        "Search shortcut did not reach " + state.value,
                    )

                def activate_character(character):
                    context = api.foreground()
                    translated = int(key_scan(character, api.active(context.thread)))
                    assert translated != -1
                    open_search(translated & 255, shift=bool(translated & 256))
                    close_search()
                    return translated

                configure("CTRL+ALT+semicolon")
                wait_status(GlobalShortcutsState.BOUND)
                report["punctuation_keys"] = {"us": activate_character(";")}
                for name in ("fr", "us"):
                    select(wanted[name], "punctuation-" + name)
                    report["punctuation_keys"][name] = activate_character(";")
                    publish()
                configure("CTRL+ALT+eacute")
                wait_status(GlobalShortcutsState.ERROR)
                assert call(lambda: search.shortcut_status.binding) is None
                select(wanted["fr"], "unavailable-recovery-fr")
                wait_status(GlobalShortcutsState.BOUND)
                report["recovered_character_key"] = activate_character("é")
                select(wanted["us"], "unavailable-us")
                wait_status(GlobalShortcutsState.ERROR)
                native.key(0x32, control=True, alt=True)
                time.sleep(0.3)
                assert not search_window(), "Unavailable layout retained its old key"
                select(wanted["fr"], "unavailable-recovery-fr-again")
                wait_status(GlobalShortcutsState.BOUND)
                activate_character("é")
                report["unavailable_layout_recovered"] = True

                competitor = WindowsHotkeyAPI()
                competitor.register(MOD_CONTROL | MOD_ALT, 0x20)
                try:
                    configure("CTRL+ALT+space")
                    wait_status(GlobalShortcutsState.ERROR)
                    assert call(lambda: search.shortcut_status.binding) is None
                    assert call(lambda: search.shortcut_status.message) == (
                        "shortcut is already in use"
                    )
                finally:
                    competitor.unregister()
                wait_status(GlobalShortcutsState.BOUND)
                open_search(0x20)
                close_search()
                report["conflict_recovered"] = True
                select(wanted["us"], "shutdown-unavailable-us")
                configure("CTRL+ALT+eacute")
                wait_status(GlobalShortcutsState.ERROR)
                report["shutdown_with_unavailable_layout"] = True
                report["ok"] = True
                return
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
            context = api.foreground()
            assert context is not None
            assert api.request(
                context,
                next(h for h in loaded_before if _code(h) == wanted["us"]),
                threading.Event(),
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
                        "target_visible": (
                            bool(native.user.IsWindowVisible(target.root))
                            if target
                            else None
                        ),
                        "recent_contexts": [
                            {
                                "context": context.__dict__,
                                "visible": bool(
                                    native.user.IsWindowVisible(context.root)
                                ),
                            }
                            for context in backend._contexts
                        ],
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
            cleanup = CleanupJournal(report, errors, publish)
            for hwnd in owned_windows:

                def close_window(hwnd=hwnd):
                    if native.user.IsWindow(hwnd):
                        assert native.user.PostMessageW(hwnd, 0x10, 0, 0)

                cleanup.attempt(f"owned-window-{hwnd}", close_window)
            if notepad is not None:

                def restore_editor_layout():
                    focus_editor()
                    context = api.foreground()
                    assert context is not None
                    assert api.request(
                        context, shared["editor_original"], threading.Event()
                    )
                    native.wait_for(
                        lambda: api.active(context.thread) == shared["editor_original"],
                        "Notepad input layout did not restore",
                    )
                    report["editor_layout_restored"] = True

                cleanup.attempt("editor-layout", restore_editor_layout)
                if owned_editor is None:

                    def close_notepad():
                        focus_editor()
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
                                    w["hwnd"] == notepad["hwnd"]
                                    for w in native.windows()
                                )
                            ),
                            "Owned Notepad did not close",
                            20,
                        )
                        report["notepad_closed"] = True

                    cleanup.attempt("stock-editor-close", close_notepad)
            if owned_editor is not None:

                def close_owned_editor():
                    owned_editor.close()
                    report["editor_closed"] = True

                cleanup.attempt("owned-editor-close", close_owned_editor)
            if ready.is_set():

                def close():
                    grab = Gtk.grab_get_current()
                    if isinstance(grab, Gtk.MenuShell):
                        grab.cancel()
                    shared["ui"].window.close()

                cleanup.attempt("dock-close", lambda: call(close))
            cleanup.attempt("driver-report", lambda: None)

    with trace_requests(report.setdefault("request_trace", {}), publish):
        driver = threading.Thread(
            target=drive, name="windows-keyboardlayout-smoke", daemon=False
        )
        driver.start()
        try:
            app.main()
            driver.join(90)
            assert not driver.is_alive()
            assert native.workarea() == workarea
        except Exception as error:
            errors.append(error)
            report.update(ok=False, main_error=repr(error))
        finally:
            cleanup = CleanupJournal(report, errors, publish)

            def restore_caller():
                # Restore only the process's original input locale, never any
                # preferred-language or keyboard registry setting.
                assert activate(original_thread, 0x100)
                assert api.active(0) & 0xFFFFFFFF == original_thread & 0xFFFFFFFF
                report["caller_layout_restored"] = True

            cleanup.attempt("caller-layout", restore_caller)
            released = []
            for handle in reversed(owned):

                def release(handle=handle):
                    accepted = bool(unload(handle))
                    released.append(accepted)
                    assert accepted, "Owned keyboard layout did not unload"

                cleanup.attempt("owned-layout-" + _code(handle), release)
            report["owned_layouts_released"] = all(released) and len(released) == len(
                owned
            )

            def loaded_set():
                report["loaded_set_restored"] = set(api.layouts()) == set(loaded_before)
                assert report["loaded_set_restored"], (
                    "Loaded keyboard layouts did not restore"
                )

            cleanup.attempt("loaded-set", loaded_set)

            def preferences():
                restored = oracle(preferences_only=True)
                report["restored"] = restored
                report["preferences_restored"] = all(
                    restored[k] == before[k]
                    for k in ("default", "preload", "substitutes", "wheel_route")
                )
                report["independent_loaded_set_restored"] = {
                    row["code"] for row in restored["loaded"]
                } == {row["code"] for row in before["loaded"]}
                assert report["preferences_restored"], (
                    "Input preferences did not restore"
                )
                assert report["independent_loaded_set_restored"], (
                    "Independent loaded set did not restore"
                )

            cleanup.attempt("independent-preferences", preferences)

            def normal_shutdown():
                report["normal_shutdown"] = (
                    shared.get("applet") is not None
                    and shared["applet"]._stopped
                    and shared["applet"]._timer_id == 0
                )
                assert report["normal_shutdown"], (
                    "Keyboard applet did not stop normally"
                )

            cleanup.attempt("normal-shutdown", normal_shutdown)
            if args.search_input:
                released_shortcuts = []
                for key in (0x20, 0xBA, 0xBE, 0x32):

                    def check_shortcut(key=key):
                        probe = WindowsHotkeyAPI()
                        try:
                            probe.register(MOD_CONTROL | MOD_ALT, key)
                        finally:
                            probe.unregister()
                        released_shortcuts.append(key)

                    cleanup.attempt(f"shortcut-{key:02x}", check_shortcut)
                report["search_shortcuts_released"] = len(released_shortcuts) == 4
            KeyboardLayoutApplet._select_layout = original_select
            KeyboardLayoutApplet.on_scroll = original_scroll
            WindowsMenuFocus.restore = original_restore
            if not report.get("direct_requests", {}).get("controls_passed", False):
                errors.append(
                    AssertionError("Direct input request controls did not pass")
                )
            if errors:
                report["ok"] = False
                report.setdefault("error", repr(errors[0]))
            try:
                publish()
                print(json.dumps(report, indent=2))
            except Exception as error:
                errors.append(error)
        if errors:
            raise errors[0]


if __name__ == "__main__":
    if sys.platform != "win32":
        main({})
    else:
        from tools.windows.visual.input_environment import text_services

        monitor = {}
        try:
            with text_services() as monitor:
                main(monitor)
        finally:
            if "output_directory" in monitor:
                from tools.windows.visual.desktop_driver import write_shared_json

                write_shared_json(
                    Path(monitor["output_directory"]) / "text-services.json", monitor
                )
