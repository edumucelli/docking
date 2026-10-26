"""Verify native and live Search artwork in the complete Windows application.

A gate delays one real shell extraction, never its pixels. Native keyboard
input opens Search; main-loop observations verify that completion updates the
existing row with native artwork without rebuilding results or changing focus.
Further stages cover current applet and live-window icons and normal cleanup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
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
    from gi.repository import GLib
    from PIL import ImageGrab

    import docking.app as app
    from docking.platform.backends.windows import icons
    from docking.platform.backends.windows.hotkeys import (
        MOD_ALT,
        MOD_CONTROL,
        WindowsHotkeyAPI,
    )
    from docking.search.services.global_shortcuts import GlobalShortcutsState
    from tools.windows.desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    assert not any(
        w["title"] == "Docking" or w["class"] == "Notepad" for w in native.windows()
    ), "Close an existing dock or Notepad before the owned fixture starts"
    baseline = native.workarea()
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
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
        ),
        encoding="utf-8",
    )
    ready, blocked, release = threading.Event(), threading.Event(), threading.Event()
    shared, report, errors, held_target = {}, {"baseline_workarea": baseline}, [], []
    original_build = app.build_dock_window
    original_extract = icons.extract_icon

    def extract(target, size):
        if (
            size == 32
            and held_target
            and target == held_target[0]
            and not release.is_set()
        ):
            blocked.set()
            assert release.wait(30), (
                "Timed out waiting to release real shell extraction"
            )
        return original_extract(target, size)

    icons.extract_icon = extract

    def build(**kwargs):
        ui = original_build(**kwargs)
        shared.update(ui=ui, kwargs=kwargs)
        ready.set()
        return ui

    app.build_dock_window = build

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

    def fingerprint(image):
        if image is None:
            return None
        channels = image.get_n_channels()
        stride = image.get_rowstride()
        pixels = bytes(image.get_pixels())
        packed = b"".join(
            pixels[y * stride : y * stride + image.get_width() * channels]
            for y in range(image.get_height())
        )
        return {
            "size": [image.get_width(), image.get_height()],
            "channels": channels,
            "sha256": hashlib.sha256(packed).hexdigest(),
        }

    def rows(provider=None, desktop_id=None):
        return [
            row
            for row in shared["ui"].search.window.results_list.get_children()
            if (provider is None or row.result.identity.provider_id == provider)
            and (
                desktop_id is None
                or dict(row.result.metadata).get("desktop_id") == desktop_id
            )
        ]

    def search_window():
        return next(
            (w for w in native.windows(os.getpid()) if w["title"] == "Docking Search"),
            None,
        )

    def query(text):
        if not search_window():
            native.key(0x20, control=True, alt=True)
            native.wait_for(search_window, "Native shortcut did not open Search")
        native.wait_for(
            lambda: native.user.GetForegroundWindow() == search_window()["hwnd"],
            "Search did not gain foreground focus: " + str(native.windows()),
        )
        native.key(ord("A"), control=True)
        native.type_text(text)
        native.wait_for(
            lambda: ui_call(lambda: shared["ui"].search._current_query) == text,
            "Typing did not update the Search query",
        )
        native.wait_for(
            lambda: (
                not ui_call(
                    lambda: (
                        shared["ui"].search._coordinator.snapshot().pending_provider_ids
                    )
                )
            ),
            "Search providers did not settle",
        )

    def capture(name):
        ImageGrab.grab(all_screens=True).save(output / (name + ".png"))

    def drive():
        editor = None
        try:
            assert ready.wait(90), "The complete dock did not start"
            ui, kwargs = shared["ui"], shared["kwargs"]
            native.wait_for(
                lambda: (
                    ui_call(
                        lambda: (
                            ui.search.shortcut_status.state
                            if ui.search.shortcut_status
                            else None
                        )
                    )
                    is GlobalShortcutsState.BOUND
                ),
                "Native Search shortcut did not bind",
            )
            native.key(0x1B)  # Dismiss any shell menu left by an earlier probe.
            native.click(1100, 100)
            report["desktop_foreground_before_search"] = (
                native.user.GetForegroundWindow()
            )
            app_id = ui_call(
                lambda: next(
                    a.desktop_id
                    for a in kwargs["application_registry"].snapshot()
                    if a.name.lower() == "notepad"
                )
            )
            held_target.append("shell:AppsFolder\\" + app_id)
            report["application_id"] = app_id
            query("notepad")
            assert blocked.wait(10), "Search did not request native 32px artwork"

            def hold_row():
                row = next(iter(rows("applications", app_id)))
                shared["held_row"] = row
                window = ui.search.window
                return {
                    "row": id(row),
                    "image": fingerprint(row.image.get_pixbuf()),
                    "generation": window._result_generation,
                    "selection": str(window.selected_result().identity),
                    "focus": window.window.get_focus() is window.search_entry,
                    "query": window.search_entry.get_text(),
                }

            before = ui_call(hold_row)
            capture("shell-icon-pending")
            release.set()

            def native_ready():
                cache = kwargs["icon_loader"]._windows_icons
                row = shared["held_row"]
                image = cache._images.get((app_id, 32)) if cache else None
                return (
                    image is not None
                    and fingerprint(row.image.get_pixbuf()) != before["image"]
                )

            native.wait_for(
                lambda: ui_call(native_ready),
                "Real native artwork did not replace the pending fallback",
                30,
            )

            def verify_completion():
                row, window = shared["held_row"], ui.search.window
                expected = kwargs["icon_loader"].load_desktop_icon(
                    kwargs["application_registry"].get(app_id), 32
                )
                assert row in rows("applications", app_id)
                assert fingerprint(row.image.get_pixbuf()) == fingerprint(expected)
                assert id(row) == before["row"]
                assert window._result_generation == before["generation"]
                assert str(window.selected_result().identity) == before["selection"]
                assert window.search_entry.get_text() == before["query"]
                assert window.window.get_focus() is window.search_entry
                return fingerprint(row.image.get_pixbuf())

            report["native_completion"] = ui_call(verify_completion)
            report["completion_preserves_rows_selection_query_focus"] = True
            capture("native-notepad-search")

            # Launch the selected real application and verify live-window artwork.
            native.key(0x0D)
            editor = native.wait_for(
                lambda: next(
                    (w for w in native.windows() if w["class"] == "Notepad"), None
                ),
                "Search did not launch Notepad",
                40,
            )
            native.wait_for(
                lambda: not search_window(),
                "Application activation did not hide Search",
            )
            query("notepad")

            def live_ready():
                cache = kwargs["icon_loader"]._windows_icons
                return bool(rows("windows", app_id)) and any(
                    key[0] == app_id and key[1] != 32 and image is not None
                    for key, image in cache._images.items()
                )

            native.wait_for(
                lambda: ui_call(live_ready),
                "Live-window native artwork did not become ready",
                30,
            )

            def verify_live():
                result = []
                for row in rows(desktop_id=app_id):
                    expected = ui.search.window._result_icons.load(row.result, 32)
                    actual = fingerprint(row.image.get_pixbuf())
                    assert actual == fingerprint(expected)
                    result.append(
                        {"provider": row.result.identity.provider_id, "image": actual}
                    )
                assert any(row["provider"] == "windows" for row in result)
                return result

            report["live_application_and_window"] = ui_call(verify_live)
            capture("native-live-window-search")
            query("clock")

            def applet_ready():
                return bool(rows("dock", "applet://clock")) and any(
                    row.result.title == "Windows Clock" for row in rows("applications")
                )

            native.wait_for(
                lambda: ui_call(applet_ready),
                "Clock application and pinned applet results did not appear",
            )

            def clock_native_ready():
                cache = kwargs["icon_loader"]._windows_icons
                row = next(
                    row
                    for row in rows("applications")
                    if row.result.title == "Windows Clock"
                )
                desktop_id = dict(row.result.metadata)["desktop_id"]
                return cache._images.get((desktop_id, 32)) is not None

            native.wait_for(
                lambda: ui_call(clock_native_ready),
                "Windows Clock shell artwork did not arrive",
                30,
            )

            def verify_clock():
                row = rows("dock", "applet://clock")[0]
                expected = ui.search.window._result_icons.load(row.result, 32)
                actual = fingerprint(row.image.get_pixbuf())
                assert actual == fingerprint(expected)
                generic = fingerprint(
                    kwargs["icon_loader"].load_icon("application-x-executable", 32)
                )
                assert actual != generic
                return {"applet": actual, "generic": generic}

            report["clock_applet"] = ui_call(verify_clock)
            capture("clock-applet-and-native-app")
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            report["failure_foreground"] = native.user.GetForegroundWindow()
            report["failure_windows"] = native.windows()
            capture("failure")
        finally:
            release.set()
            if editor:
                for row in native.windows(editor["pid"]):
                    native.user.PostMessageW(row["hwnd"], 0x10, 0, 0)
            if ready.is_set():
                ui_call(lambda: shared["ui"].window.close())

    driver = threading.Thread(
        target=drive, name="windows-search-icon-smoke", daemon=True
    )
    driver.start()
    try:
        app.main()
        driver.join(10)
        assert not driver.is_alive(), "Desktop driver survived normal shutdown"
        native.wait_for(
            lambda: native.workarea() == baseline,
            "Normal shutdown did not restore the work area",
        )
        hotkey = WindowsHotkeyAPI()
        hotkey.register(MOD_CONTROL | MOD_ALT, 0x20)
        hotkey.unregister()
        report["normal_shutdown"] = True
        if errors:
            raise errors[0]
    finally:
        (output / "result.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
