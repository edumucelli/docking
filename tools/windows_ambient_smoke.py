"""Exercise Ambient with native Windows input and actual QEMU audio capture.

Start this guest tool, then run windows_vm_audio.py --share ... --name NAME on
the host. Each phase waits for measured audio from the VM's real output relay.
Bundled OGG playback uses the normal source path; only the short loop phase
uses an explicitly generated OGG fixture. No audio sink is replaced.
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
from ctypes import wintypes
from itertools import pairwise
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
    # Share installed dependency metadata, keeping app preferences and runtime
    # caches isolated. Rebuilding a cold full plugin registry is a setup check.
    # Query GLib in a short-lived process before changing XDG_CACHE_HOME. Its
    # Windows default can be INetCache, and the value is cached per process.
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
    installed_cache = Path(json.loads(cached.stdout)) / "gstreamer-1.0"
    registries = list(installed_cache.glob("registry.*.bin"))
    if not registries:
        parser.error("Prepare the native GStreamer registry before this UI smoke")
    target_cache = output / "cache/gstreamer-1.0"
    target_cache.mkdir(parents=True, exist_ok=True)
    for registry in registries:
        shutil.copy2(registry, target_cache / registry.name)
    os.environ["XDG_CONFIG_HOME"] = str(output / "config")
    os.environ["XDG_CACHE_HOME"] = str(output / "cache")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from docking.launcher import prepare_display

    prepare_display()
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst, Gtk
    from PIL import ImageGrab

    import docking.app as app
    import docking.applets.ambient.applet as ambient
    from docking.platform.backends.windows.hotkeys import (
        MOD_ALT,
        MOD_CONTROL,
        WindowsHotkeyAPI,
    )
    from tools.windows_desktop_driver import WindowsDesktop

    config = output / "config/docking/dock.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "pinned": [
                    {"kind": "applet", "target": "applet://ambient"},
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
    native.key(0x1B)
    baseline = native.workarea()
    sound = ctypes.WinDLL("winmm")
    sound.waveOutGetNumDevs.restype = ctypes.c_uint
    report = {
        "baseline_workarea": baseline,
        "gstreamer": Gst.version_string(),
        "wave_output_devices": sound.waveOutGetNumDevs(),
        "audio": {},
        "registry_seed": [registry.name for registry in registries],
    }
    shared, errors = {}, []
    original_file_pipeline = ambient._build_file_pipeline

    def observed_file_pipeline(**kwargs):
        pipeline = original_file_pipeline(**kwargs)
        if pipeline is not None:
            pipeline.get_bus().connect(
                "message::error",
                lambda _bus, message: report.setdefault("pipeline_errors", []).append(
                    str(message.parse_error()[0])
                ),
            )
        return pipeline

    ambient._build_file_pipeline = observed_file_pipeline
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
        assert done.wait(20), "GTK did not service a queued observation"
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
            assert measured["frames"] >= 48000, measured
            assert measured["rms"] > 0.0001, measured
        else:
            assert measured["rms"] < 0.0001, measured

    def elements(pipeline):
        iterator = pipeline.iterate_recurse()
        result = []
        while True:
            state, value = iterator.next()
            if state == Gst.IteratorResult.DONE:
                return result
            if state == Gst.IteratorResult.RESYNC:
                iterator.resync()
                result.clear()
                continue
            assert state == Gst.IteratorResult.OK
            if not isinstance(value, Gst.Element):
                value = value.get_object()
            result.append(value)

    def pipeline_info(applet):
        pipeline = applet._pipeline
        assert pipeline is not None
        change, state, _pending = pipeline.get_state(0)
        factories, waves = [], []
        for element in elements(pipeline):
            factory = element.get_factory()
            if factory is not None:
                name = factory.get_name()
                factories.append(name)
                if name == "audiotestsrc":
                    waves.append(element.get_property("wave").value_nick)
        return {
            "change": change.value_nick,
            "state": state.value_nick,
            "factories": factories,
            "waves": waves,
        }

    def make_loop_fixture():
        folder = output / "loop fixture résumé"
        folder.mkdir(exist_ok=True)
        pipeline = Gst.Pipeline.new("short-ogg-fixture")
        parts = [
            Gst.ElementFactory.make(factory, "fixture-" + factory)
            for factory in (
                "audiotestsrc",
                "audioconvert",
                "vorbisenc",
                "oggmux",
                "filesink",
            )
        ]
        assert all(parts), "Missing OGG fixture encoder"
        parts[0].set_property("num-buffers", 50)
        parts[0].set_property("wave", 5)
        parts[-1].set_property("location", str(folder / "birds.ogg"))
        for part in parts:
            pipeline.add(part)
        for left, right in pairwise(parts):
            assert left.link(right)
        try:
            assert (
                pipeline.set_state(Gst.State.PLAYING) != Gst.StateChangeReturn.FAILURE
            )
            message = pipeline.get_bus().timed_pop_filtered(
                20 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR
            )
            assert message is not None and message.type == Gst.MessageType.EOS
        finally:
            pipeline.set_state(Gst.State.NULL)
        return folder

    def drive():
        try:
            assert ready.wait(90)
            assert report["wave_output_devices"] > 0, "No Windows audio output device"
            ui, model = shared["ui"], shared["model"]
            applet = call(lambda: model.get_applet("applet://ambient"))
            assert applet is not None, "Ambient failed to instantiate"
            dock = native.wait_for(
                lambda: next(
                    (w for w in native.windows(os.getpid()) if w["title"] == "Docking"),
                    None,
                ),
                "Docking did not map",
            )
            report["initial_dock"] = dock

            def point():
                frame = ui.window.current_interaction_frame()
                item = next(
                    g
                    for g in frame.item_geometries
                    if g.item.desktop_id == "applet://ambient"
                )
                rect = item.draw_rect
                current = next(
                    w for w in native.windows(os.getpid()) if w["hwnd"] == dock["hwnd"]
                )
                return (
                    current["rect"][0] + rect.x + rect.w / 2,
                    current["rect"][1] + rect.y + rect.h / 2,
                )

            def playing():
                return call(lambda: applet._pipeline is not None and applet._playing)

            def settled():
                def active():
                    info = call(lambda: pipeline_info(applet))
                    report["latest_pipeline"] = info
                    if report.get("pipeline_errors"):
                        raise RuntimeError(str(report["pipeline_errors"]))
                    return info["state"] == "playing"

                native.wait_for(
                    active,
                    "GStreamer did not enter PLAYING",
                    20,
                )
                info = call(lambda: pipeline_info(applet))
                assert {"wasapi2sink", "wasapisink"} & set(info["factories"]), info
                return info

            def select(index, expected):
                position = call(point)
                report.setdefault("menu_points", []).append(
                    {"sound": expected, "point": position}
                )
                native.click(*position, right=True)
                native.wait_for(
                    lambda: call(
                        lambda: isinstance(Gtk.grab_get_current(), Gtk.MenuShell)
                    ),
                    "Ambient menu did not open",
                )
                capture("ambient-menu-" + expected)
                native.key(0x24)
                for _ in range(index):
                    native.key(0x28)
                native.key(0x0D)
                native.wait_for(
                    lambda: call(lambda: applet._current == expected),
                    "Native menu did not select " + expected,
                )

            native.user.WindowFromPoint.argtypes = (wintypes.POINT,)
            native.user.WindowFromPoint.restype = wintypes.HWND

            def clickable():
                x, y = call(point)
                hwnd = native.user.WindowFromPoint(wintypes.POINT(int(x), int(y)))
                owner = wintypes.DWORD()
                native.user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if owner.value == os.getpid():
                    report["first_click"] = {"x": x, "y": y, "hwnd": hwnd}
                    return x, y
                return None

            first = native.wait_for(clickable, "Ambient icon did not become clickable")
            native.click(*first)
            native.wait_for(playing, "Native click did not start Ambient")
            report["bundled_file"] = str(ambient.SOUNDS_DIR / "birds.ogg")
            report["birds_pipeline"] = settled()
            audio_phase("01-birds", audible=True)
            report["file_pipelines"] = {"birds": report["birds_pipeline"]}
            for index, sound in enumerate(ambient.ALL_SOUNDS[1:7], start=1):
                assert sound.kind == "file"
                select(index, sound.name)
                report["file_pipelines"][sound.name] = settled()
                audio_phase(f"{index + 1:02}-{sound.name}", audible=True)
            select(7, "white-noise")
            report["white_pipeline"] = settled()
            assert report["white_pipeline"]["waves"] == ["white-noise"]
            native.user.SetCursorPos(*map(int, call(point)))
            time.sleep(0.6)
            native.scroll(120)
            native.wait_for(
                lambda: call(lambda: abs(applet._volume - 0.6) < 1e-6),
                "Native wheel did not change volume",
            )
            capture("ambient-playing")
            audio_phase("08-white", audible=True)
            for _ in range(7):
                native.scroll(-120)
            assert call(lambda: applet._volume) == 0
            audio_phase("09-muted", audible=False)
            for _ in range(6):
                native.scroll(120)
            select(8, "pink-noise")
            report["pink_pipeline"] = settled()
            assert report["pink_pipeline"]["waves"] == ["pink-noise"]
            audio_phase("10-pink", audible=True)
            fixture = make_loop_fixture()
            loops = [0]

            def looping():
                original = applet._on_eos

                def observed(bus, message):
                    loops[0] += 1
                    return original(bus, message)

                applet._on_eos = observed
                ambient.SOUNDS_DIR = fixture

            call(looping)
            select(0, "birds")
            report["loop_pipeline"] = settled()
            audio_phase("11-loop", audible=True)
            assert call(lambda: loops[0]) >= 2, (
                "Real end-of-stream looping did not repeat"
            )
            report["loop_count"] = call(lambda: loops[0])
            native.click(*call(point))
            native.wait_for(lambda: not playing(), "Native click did not stop playback")
            assert call(lambda: applet._pipeline) is None
            assert not call(lambda: applet._bus_watching)
            audio_phase("12-stopped", audible=False)
            saved = json.loads(config.read_text(encoding="utf-8"))["applet_prefs"][
                "ambient"
            ]
            assert saved["sound"] == "birds" and abs(saved["volume"] - 0.6) < 1e-6
            report.update(native_controls=True, persisted_preferences=saved, ok=True)
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

                call(close)

    driver = threading.Thread(target=drive, name="windows-ambient-smoke", daemon=True)
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
