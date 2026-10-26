"""Exercise Music with real Windows input and an independent MediaPlayer fixture."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--shutdown-read", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Requires an owned native Windows desktop")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Use a fresh output directory")
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
    from docking.applets.music.windows import WindowsMusicBackend
    from docking.applets.volume.windows import get_state as master_state
    from tools.windows.desktop_driver import WindowsDesktop

    native = WindowsDesktop()
    assert not any(w["title"] == "Docking" for w in native.windows())
    baseline, explorer, master = native.workarea(), native.explorer(), master_state()
    native.user.SetCursorPos(1100, 40)
    ready, close_done, arm_read, held_read = (threading.Event() for _ in range(4))
    shared, errors = {}, []
    report = {"pid": os.getpid(), "stages": [], "io": []}
    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://music"},
                    {"kind": "applet", "target": "applet://clock"},
                ],
                "icon_size": 64,
                "hide_mode": "none",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        )
    )
    original_build = app.build_dock_window
    originals = {}

    def instrument(name, original):
        def invoke(backend, *values, **kwargs):
            report["io"].append({"method": name, "thread": threading.get_ident()})
            try:
                result = original(backend, *values, **kwargs)
                if name == "poll":
                    report["native_state"] = {
                        "title": result.title,
                        "status": result.playback_status,
                        "volume": result.volume_percent
                        if result.can_set_volume
                        else None,
                    }
                elif isinstance(result, bool):
                    report["io"][-1]["accepted"] = result
                if name == "poll" and arm_read.is_set():
                    held_read.set()
                    assert backend._cancelled.wait(20), "Stop did not cancel held read"
                return result
            except Exception as exc:
                report.setdefault(
                    "cancelled_io"
                    if backend._cancelled.is_set() and name != "close"
                    else "backend_errors",
                    [],
                ).append({"method": name, "error": repr(exc)})
                raise
            finally:
                if name == "close":
                    close_done.set()

        return invoke

    for name in (
        "poll",
        "play_pause",
        "next_track",
        "previous_track",
        "set_volume",
        "close",
    ):
        original = getattr(WindowsMusicBackend, name)
        originals[name] = original
        setattr(WindowsMusicBackend, name, instrument(name, original))

    def build(**kwargs):
        ui = original_build(**kwargs)
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
        assert done.wait(20), "GTK did not respond"
        if failures:
            raise failures[0]
        return values[0]

    def publish():
        (output / "result.json").write_text(json.dumps(report, indent=2))

    def stage(name):
        report["stages"].append(name)
        publish()

    def drive():
        dock = child = applet = None
        original_level = None
        folder = output / "fixture"

        def set_level(percent):
            state, backend = applet._state, applet._backend
            applet._worker.run(
                name="fixture-prepare-volume",
                fn=lambda: backend.set_volume(state, percent),
                on_result=lambda _ok: applet._refresh_now(),
            )

        try:
            assert ready.wait(90)
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Dock missing",
            )
            applet = call(lambda: shared["model"].get_applet("applet://music"))
            assert isinstance(applet._backend, WindowsMusicBackend)
            assert "docking.applets.music.linux" not in sys.modules
            child = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("music_fixture.py")),
                    "--output",
                    str(folder),
                    "--timeout",
                    "180",
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            def fixture():
                try:
                    return json.loads(
                        (folder / "state.json").read_text(encoding="utf-8-sig")
                    )
                except (OSError, ValueError):
                    return {}

            def snapshot():
                return call(lambda: applet._state)

            native.wait_for(
                lambda: fixture().get("state") == "Playing", "Fixture never played", 40
            )
            native.wait_for(
                lambda: (
                    snapshot().available
                    and snapshot().title == "Docking generated track 1"
                ),
                "No native media metadata",
                30,
            )
            native.wait_for(
                lambda: snapshot().can_set_volume,
                "Player volume could not be attributed",
                20,
            )
            native.wait_for(
                lambda: call(lambda: applet._album_art is not None),
                "Native artwork missing",
            )
            original_level = snapshot().volume_percent
            report.update(
                gtk_scale=call(lambda: shared["ui"].window.get_scale_factor()),
                original_player_volume=original_level,
            )

            def point():
                window = shared["ui"].window
                frame = window.geometry.build_frame(main_cursor=-1.0)
                rect = next(
                    g.draw_rect
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://music"
                )
                bounds = next(
                    w["rect"]
                    for w in native.windows(os.getpid())
                    if w["hwnd"] == dock["hwnd"]
                )
                scale = window.get_scale_factor()
                return bounds[0] + (rect.x + rect.w / 2) * scale, bounds[1] + (
                    rect.y + rect.h / 2
                ) * scale

            def click(right=False):
                native.user.SetCursorPos(1100, 40)
                time.sleep(0.5)
                p = call(point)
                assert native.window_at(*p) == dock["hwnd"]
                native.click(*p, right=right)

            def menu(index):
                click(True)
                native.wait_for(
                    lambda: call(lambda: isinstance(Gtk.grab_get_current(), Gtk.Menu)),
                    "Music menu missing",
                )
                native.key(0x24)
                for _ in range(index):
                    native.key(0x28)
                native.key(0x0D)

            def volume_at(percent):
                return (
                    snapshot().volume_percent == percent
                    and report.get("native_state", {}).get("volume") == percent
                )

            def cover(color):
                return call(
                    lambda: (
                        applet._album_art is not None
                        and tuple(applet._album_art.get_pixels()[:3]) == color
                    )
                )

            assert cover((20, 60, 110))
            click()
            native.wait_for(
                lambda: fixture().get("state") == "Paused",
                "Real click did not pause MediaPlayer",
            )
            native.wait_for(
                lambda: snapshot().playback_status == "Paused", "Paused UI missing"
            )
            stage("native-click-pauses-real-player")
            click()
            native.wait_for(
                lambda: fixture().get("state") == "Playing", "Real click did not resume"
            )
            native.wait_for(
                lambda: snapshot().playback_status == "Playing", "Playing UI missing"
            )
            menu(2)
            native.wait_for(
                lambda: fixture().get("index") == 1, "Next menu did not change track"
            )
            native.wait_for(
                lambda: (
                    snapshot().title == "Docking generated track 2"
                    and cover((180, 40, 80))
                ),
                "Next metadata/artwork missing",
            )
            native.user.SetCursorPos(1100, 40)
            time.sleep(0.6)
            ImageGrab.grab(all_screens=True).save(output / "music-track-two.png")
            stage("native-menu-next-metadata-artwork")
            menu(1)
            native.wait_for(
                lambda: fixture().get("index") == 0, "Previous did not change track"
            )
            native.wait_for(
                lambda: (
                    snapshot().title == "Docking generated track 1"
                    and cover((20, 60, 110))
                ),
                "Previous metadata/artwork missing",
            )
            stage("native-menu-previous")

            # Prepare a midrange level on this owned fixture, then exercise
            # actual wheel/menu input in both directions with native readback.
            call(lambda: set_level(50))
            native.wait_for(lambda: volume_at(50), "Fixture volume setup failed")
            click(True)
            native.wait_for(
                lambda: call(lambda: isinstance(Gtk.grab_get_current(), Gtk.Menu)),
                "Volume menu missing",
            )
            labels = call(
                lambda: [
                    (item.get_label(), item.get_sensitive())
                    for item in Gtk.grab_get_current().get_children()
                    if isinstance(item, Gtk.MenuItem)
                    and not isinstance(item, Gtk.SeparatorMenuItem)
                ]
            )
            report["menu"] = labels
            assert ("Volume Up", True) in labels and ("Volume Down", True) in labels
            native.key(0x1B)
            native.user.SetCursorPos(*map(int, call(point)))
            native.scroll(-120)
            native.wait_for(
                lambda: volume_at(45),
                "Wheel down did not set player volume",
            )
            menu(3)
            native.wait_for(lambda: volume_at(50), "Volume Up menu failed")
            menu(4)
            native.wait_for(lambda: volume_at(45), "Volume Down menu failed")
            native.user.SetCursorPos(*map(int, call(point)))
            native.scroll(120)
            native.wait_for(lambda: volume_at(50), "Wheel up failed")
            assert master_state() == master and abs(fixture()["volume"] - 0.15) < 0.001
            stage("native-wheel-and-volume-menus-preserve-master-and-source-volume")
            call(lambda: set_level(original_level))
            native.wait_for(
                lambda: volume_at(original_level),
                "Owned player volume was not restored",
            )
            original_level = None
            report["fixture"] = fixture()
            assert report["fixture"]["commands"] == [
                "Pause",
                "Play",
                "Next",
                "Previous",
            ]
            if args.shutdown_read:
                arm_read.set()
                call(applet._refresh_now)
                assert held_read.wait(20), (
                    "No native read reached the controlled barrier"
                )
                assert call(lambda: shared["ui"].window.get_visible())
            assert native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
            native.wait_for(
                lambda: not native.user.IsWindow(dock["hwnd"]), "Dock did not close"
            )
            assert close_done.wait(20), "Owned media interfaces did not close"
            stage(
                "normal-close-during-native-read"
                if args.shutdown_read
                else "normal-close-releases-media-worker"
            )
            report["ok"] = True
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            if applet is not None:
                report["last_ui"] = call(
                    lambda: {
                        "title": applet._state.title,
                        "status": applet._state.playback_status,
                        "art_bytes": len(applet._state.art_data),
                        "decoded_art": applet._album_art is not None,
                        "volume": applet._state.volume_percent,
                    }
                )
            ImageGrab.grab(all_screens=True).save(output / "failure.png")
        finally:
            if applet is not None and original_level is not None:
                # Explicit cleanup belongs to the owned fixture only.
                try:
                    call(lambda: set_level(original_level))
                    native.wait_for(
                        lambda: volume_at(original_level),
                        "Player cleanup did not finish",
                        5,
                    )
                except (AssertionError, RuntimeError) as exc:
                    report["volume_cleanup_error"] = repr(exc)
            if dock is not None and native.user.IsWindow(dock["hwnd"]):
                native.user.PostMessageW(dock["hwnd"], 0x10, 0, 0)
                close_done.wait(20)
            if child is not None and child.poll() is None:
                (folder / "stop").write_text("normal close")
                report["fixture_exit"] = child.wait(20)
            elif child is not None:
                report["fixture_exit"] = child.returncode
            publish()

    driver = threading.Thread(target=drive, name="windows-music-smoke", daemon=False)
    driver.start()
    try:
        app.main()
        driver.join(45)
        assert not driver.is_alive()
        assert native.workarea() == baseline and native.explorer() == explorer
        assert report.get("fixture_exit") == 0
        assert not report.get("backend_errors")
        assert all(row.get("accepted", True) for row in report["io"])
        assert len({row["thread"] for row in report["io"]}) == 1
        assert report["io"][0]["thread"] != threading.get_ident()
        report.update(normal_shutdown=True, workarea_after=baseline)
        if errors:
            raise errors[0]
    finally:
        app.build_dock_window = original_build
        for name, original in originals.items():
            setattr(WindowsMusicBackend, name, original)
        publish()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
