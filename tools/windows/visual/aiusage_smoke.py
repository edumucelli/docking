"""Exercise AI Usage on Windows with real headless hooks and native UI input.

Provider settings and transcript fixtures belong only to this output directory.
The normal applet registers its real Windows commands, and the generated Claude
exec hook ingests a fixture while the complete dock runs. No external AI service
or real provider session is required or modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
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
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.applets.aiusage.backends import claude, codex
    from docking.applets.aiusage.state import Provider, today_tokens
    from docking.platform.native.windows.hotkeys import (
        MOD_ALT,
        MOD_CONTROL,
        WindowsHotkeyAPI,
    )
    from tools.windows.visual.desktop_driver import WindowsDesktop

    # The applet's normal startup performs registration against these fixtures.
    claude._CLAUDE_SETTINGS = output / "claude/settings.json"
    codex._CODEX_CONFIG = output / "codex/config.toml"
    codex._CODEX_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    codex._CODEX_CONFIG.write_text("# isolated native hook fixture\n", encoding="utf-8")
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://aiusage"},
                    {"kind": "applet", "target": "applet://clock"},
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
    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline = native.workarea()
    shared, errors, report = {}, [], {"baseline_workarea": baseline}
    ready = threading.Event()
    original_build = app.build_dock_window

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

    def capture(name):
        ImageGrab.grab(all_screens=True).save(output / (name + ".png"))

    def drive():
        dock = None
        try:
            assert ready.wait(90)
            ui, model = shared["ui"], shared["kwargs"]["model"]
            applet = ui_call(lambda: model.get_applet("applet://aiusage"))
            assert applet is not None, "AI Usage failed to instantiate"
            native.wait_for(
                lambda: claude._CLAUDE_SETTINGS.exists(),
                "Native applet startup did not register hooks",
            )
            native.wait_for(
                lambda: ui_call(lambda: applet._timer_id != 0),
                "AI Usage polling did not start",
            )
            registration = json.loads(
                claude._CLAUDE_SETTINGS.read_text(encoding="utf-8")
            )
            hook = next(
                h
                for e in registration["hooks"]["Stop"]
                for h in e["hooks"]
                if "hook_runner.py" in " ".join(h.get("args", []))
            )
            command = [hook["command"], *hook["args"]]
            assert Path(command[0]).name.casefold() == "python.exe"
            report["claude_exec_hook"] = command
            report["codex_notify"] = codex._CODEX_CONFIG.read_text(encoding="utf-8")
            before = ui_call(
                lambda: hashlib.sha256(bytes(applet.item.icon.get_pixels())).hexdigest()
            )
            transcript = output / "session résumé # 100% +.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "model": "claude-sonnet-4-6",
                            "usage": {"input_tokens": 1200, "output_tokens": 300},
                        },
                    }
                ),
                encoding="utf-8",
            )
            foreground = native.user.GetForegroundWindow()
            completed = subprocess.run(
                command,
                input=json.dumps(
                    {
                        "session_id": "windows-fixture",
                        "transcript_path": str(transcript),
                    }
                ),
                text=True,
                encoding="utf-8",
                capture_output=True,
                cwd=output,
                env=os.environ.copy(),
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=30,
            )
            assert completed.returncode == 0, completed.stderr
            assert native.user.GetForegroundWindow() == foreground, (
                "Background hook changed foreground focus"
            )
            saved = json.loads(config.read_text(encoding="utf-8"))
            assert saved["applet_prefs"]["aiusage"]["days"][0]["sessions"] == 1
            ui_call(applet._tick)  # Run the normal poll now, rather than waiting 60s.
            assert ui_call(lambda: today_tokens(state=applet._state)) == 1500
            after = ui_call(
                lambda: hashlib.sha256(bytes(applet.item.icon.get_pixels())).hexdigest()
            )
            assert after != before
            report.update(
                tokens=1500, icon_updated=True, background_hook_keeps_focus=True
            )
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Docking missing",
            )

            def item_point():
                frame = ui.window.current_interaction_frame()
                item = next(
                    g
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://aiusage"
                )
                rect = item.draw_rect
                return dock["rect"][0] + rect.x + rect.w / 2, dock["rect"][
                    1
                ] + rect.y + rect.h / 2

            x, y = ui_call(item_point)
            native.user.SetCursorPos(int(x), int(y))
            time.sleep(0.7)
            native.scroll(120)
            native.wait_for(
                lambda: ui_call(lambda: applet._selected_provider is Provider.CLAUDE),
                "Native wheel did not select Claude",
            )
            report["native_scroll_provider"] = "claude"
            time.sleep(0.5)
            capture("aiusage-tooltip")
            native.click(*ui_call(item_point), right=True)
            native.wait_for(
                lambda: ui_call(
                    lambda: isinstance(Gtk.grab_get_current(), Gtk.MenuShell)
                ),
                "AI Usage context menu did not open",
            )
            capture("aiusage-menu")
            native.key(0x1B)
            native.wait_for(
                lambda: ui_call(lambda: Gtk.grab_get_current() is None),
                "AI Usage menu did not dismiss",
            )
            report["native_context_menu"] = True
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            capture("failure")
        finally:
            if ready.is_set():

                def close():
                    grab = Gtk.grab_get_current()
                    if isinstance(grab, Gtk.MenuShell):
                        grab.cancel()
                    shared["ui"].window.close()

                ui_call(close)

    driver = threading.Thread(target=drive, name="windows-aiusage-smoke", daemon=True)
    driver.start()
    try:
        app.main()
        driver.join(10)
        assert not driver.is_alive()
        assert native.workarea() == baseline
        binding = WindowsHotkeyAPI()
        binding.register(MOD_CONTROL | MOD_ALT, 0x20)
        binding.unregister()
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
