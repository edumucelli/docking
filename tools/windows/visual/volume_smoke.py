"""Check native Volume controls against Windows keys, Settings and real audio.

Run python -m tools.windows.visual.vm_audio --share ... --name NAME concurrently
on the host.
The fixture plays Ambient white noise through its normal WASAPI sink and
restores the original endpoint scalar/mute state, including on failure.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
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
    if (output / "result.json").exists():
        parser.error("Choose a fresh output directory; keep earlier evidence")
    output.mkdir(parents=True, exist_ok=True)
    cached = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json;from gi.repository import GLib;"
                "print(json.dumps(GLib.get_user_cache_dir()))"
            ),
        ],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=30,
    )
    registries = list(
        (Path(json.loads(cached.stdout)) / "gstreamer-1.0").glob("registry.*.bin")
    )
    if not registries:
        parser.error("Prepare the complete native GStreamer registry first")
    target = output / "cache/gstreamer-1.0"
    target.mkdir(parents=True, exist_ok=True)
    for registry in registries:
        shutil.copy2(registry, target / registry.name)
    os.environ["XDG_CONFIG_HOME"] = str(output / "config")
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst, Gtk
    from PIL import ImageGrab

    import docking.app as app
    from docking.applets.volume.implementations.windows import backend as audio
    from docking.platform.native.windows.com import HRESULT, method
    from docking.platform.native.windows.hotkeys import (
        MOD_ALT,
        MOD_CONTROL,
        WindowsHotkeyAPI,
    )
    from tools.windows.visual.desktop_driver import WindowsDesktop

    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://ambient"},
                    {"kind": "applet", "target": "applet://volume"},
                    {"kind": "applet", "target": "applet://clock"},
                ],
                "applet_prefs": {"ambient": {"sound": "white-noise", "volume": 0.6}},
                "icon_size": 48,
                "hide_mode": "none",
                "show_recent_apps": False,
                "startup_tips_enabled": False,
                "update_check_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    native = WindowsDesktop()
    assert not any(w["title"] in {"Docking", "Settings"} for w in native.windows())
    native.key(0x1B)
    native.user.SetCursorPos(1100, 100)
    baseline = native.workarea()
    report = {"baseline_workarea": baseline, "audio": {}}
    shared, errors = {}, []
    ready = threading.Event()
    original_build = app.build_dock_window

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
        assert done.wait(20), "GTK did not service an observation"
        if failures:
            raise failures[0]
        return values[0]

    def capture(name):
        ImageGrab.grab(all_screens=True).save(output / (name + ".png"))

    def audio_phase(name, *, audible):
        requests = output / "requests"
        requests.mkdir(exist_ok=True)
        (requests / (name + ".json")).write_text(json.dumps({"phase": name}))
        response = output / "audio" / (name + ".json")
        native.wait_for(
            response.exists, "Host audio capture did not answer " + name, 45
        )
        measured = json.loads(response.read_text(encoding="utf-8"))
        assert "error" not in measured, measured
        report["audio"][name] = measured
        if audible:
            assert measured["frames"] >= 48000 and measured["rms"] > 0.0001, measured
        else:
            assert measured["rms"] < 0.0001, measured
        return measured

    def drive():
        original, settings = None, None
        try:
            with audio._endpoint() as endpoint:
                scalar = ctypes.c_float()
                audio._check(
                    method(endpoint, 9, HRESULT, ctypes.POINTER(ctypes.c_float))(
                        endpoint, ctypes.byref(scalar)
                    )
                )
                original = scalar.value, audio._muted(endpoint)
            report["original_endpoint"] = original
            audio.set_volume(50)
            if audio.get_state().muted:
                audio.toggle_mute()
            assert ready.wait(90)
            ui, model = shared["ui"], shared["model"]
            volume = call(lambda: model.get_applet("applet://volume"))
            ambient = call(lambda: model.get_applet("applet://ambient"))
            assert volume is not None and ambient is not None
            assert volume._backend.controls.command == "Core Audio"
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Docking did not map",
            )

            def point(applet_id):
                frame = ui.window.current_interaction_frame()
                item = next(
                    g for g in frame.item_geometries if g.item.desktop_id == applet_id
                )
                rect = item.draw_rect
                current = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                return current["rect"][0] + rect.x + rect.w / 2, current["rect"][
                    1
                ] + rect.y + rect.h / 2

            def expect(level, muted):
                native.wait_for(
                    lambda: (
                        call(lambda: (volume._volume, volume._muted)) == (level, muted)
                    ),
                    f"Applet did not reflect native endpoint {(level, muted)}",
                )
                assert audio.get_state() == (level, muted)
                report.setdefault("states", []).append({"level": level, "muted": muted})

            time.sleep(2)
            expect(50, False)
            native.click(*call(lambda: point("applet://ambient")))
            native.wait_for(
                lambda: call(
                    lambda: (
                        ambient._pipeline is not None
                        and ambient._pipeline.get_state(0)[1] == Gst.State.PLAYING
                    )
                ),
                "Ambient did not reach PLAYING",
            )
            half = audio_phase("01-master-50", audible=True)
            native.user.SetCursorPos(*map(int, call(lambda: point("applet://volume"))))
            time.sleep(0.6)
            native.scroll(120)
            expect(55, False)
            native.scroll(-120)
            expect(50, False)
            for _ in range(11):
                native.scroll(-120)
            expect(0, False)
            audio_phase("02-master-zero", audible=False)
            for _ in range(21):
                native.scroll(120)
            expect(100, False)
            full = audio_phase("03-master-100", audible=True)
            # Endpoint scalars are audio-tapered, with a driver-dependent curve.
            # Require a measured increase; 50% does not imply half amplitude.
            assert full["rms"] > half["rms"] * 1.05, (half, full)
            # These are actual shell volume keys, outside the applet backend.
            # Give Explorer a foreground target for WM_APPCOMMAND delivery.
            report["foreground_before_external_keys"] = (
                native.user.GetForegroundWindow()
            )
            native.click(1100, 100)
            report["external_key_foreground"] = native.user.GetForegroundWindow()
            native.key(0xAE)  # VK_VOLUME_DOWN
            expect(98, False)
            native.key(0xAD)  # VK_VOLUME_MUTE
            expect(98, True)
            capture("volume-muted")
            audio_phase("04-external-mute", audible=False)
            native.click(*call(lambda: point("applet://volume")))
            expect(98, False)
            audio_phase("05-native-click-unmute", audible=True)
            native.click(*call(lambda: point("applet://volume")))
            expect(98, True)
            audio_phase("06-native-click-mute", audible=False)
            native.click(1100, 100)
            native.key(0xAD)
            expect(98, False)
            # Let the shell volume flyout expire before capturing the GTK menu.
            time.sleep(4)

            # Toggle the visible level preference through the actual context menu.
            def menu():
                native.click(*call(lambda: point("applet://volume")), right=True)
                native.wait_for(
                    lambda: call(
                        lambda: isinstance(Gtk.grab_get_current(), Gtk.MenuShell)
                    ),
                    "Volume menu did not open",
                )

            menu()
            capture("volume-menu")
            native.key(0x24)
            native.key(0x0D)
            native.wait_for(
                lambda: call(lambda: volume._show_level), "Show Level not enabled"
            )
            saved = json.loads(config.read_text(encoding="utf-8"))["applet_prefs"][
                "volume"
            ]
            assert saved["show_level"]
            report["persisted_preferences"] = saved
            native.user.SetCursorPos(*map(int, call(lambda: point("applet://volume"))))
            time.sleep(1)
            capture("volume-level")
            menu()
            native.key(0x24)
            native.key(0x28)
            native.key(0x0D)
            settings = native.wait_for(
                lambda: next(
                    (w for w in native.windows() if w["title"] == "Settings"), None
                ),
                "Native Sound Settings did not open",
                60,
            )
            time.sleep(5)
            settings = native.wait_for(
                lambda: next(
                    (
                        w
                        for w in native.windows()
                        if w["title"] == "Settings"
                        and w["pid"] == settings["pid"]
                        and w["rect"][2] - w["rect"][0] > 400
                        and w["rect"][3] - w["rect"][1] > 300
                    ),
                    None,
                ),
                "Sound Settings did not finish mapping",
            )
            capture("volume-settings")
            report.update(
                settings=settings, native_controls=True, external_keys=True, ok=True
            )
        except Exception as exc:
            errors.append(exc)
            report.update(ok=False, error=repr(exc))
            capture("failure")
        finally:
            if settings is not None and any(
                w["hwnd"] == settings["hwnd"] and w["pid"] == settings["pid"]
                for w in native.windows()
            ):
                native.user.PostMessageW(settings["hwnd"], 0x10, 0, 0)
            try:
                if original is not None:
                    with audio._endpoint() as endpoint:
                        audio._check(
                            method(
                                endpoint, 7, HRESULT, ctypes.c_float, ctypes.c_void_p
                            )(endpoint, original[0], None)
                        )
                        audio._check(
                            method(
                                endpoint, 14, HRESULT, ctypes.c_int32, ctypes.c_void_p
                            )(endpoint, original[1], None)
                        )
                    report["restored_endpoint"] = list(audio.get_state())
                    assert audio.get_state() == (
                        int(original[0] * 100 + 0.5),
                        original[1],
                    )
            except Exception as exc:
                errors.append(exc)
                report.update(ok=False, restore_error=repr(exc))
            if ready.is_set():

                def close():
                    grab = Gtk.grab_get_current()
                    if isinstance(grab, Gtk.MenuShell):
                        grab.cancel()
                    shared["ui"].window.close()

                call(close)

    driver = threading.Thread(target=drive, name="windows-volume-smoke", daemon=True)
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
        temporary = output / "result.json.tmp"
        temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
        temporary.replace(output / "result.json")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
