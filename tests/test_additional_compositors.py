"""Ensure new lanes report honest backend support and require service evidence."""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.mark.parametrize("dock_present", [False, True])
def test_native_pixel_marker_excludes_fixture_titlebar_icon(dock_present):
    from tools.linux.visual.probes.native_services_probe import dock_marker_points

    width, height = 160, 64
    data = bytearray(b"\xff\x00\x00" * width * height)
    titlebar = {(x, y) for y in range(4, 28) for x in range(4, 28)}
    dock = {(x, y) for y in range(8, 56) for x in range(70, 118)}
    for x, y in titlebar | (dock if dock_present else set()):
        offset = (y * width + x) * 3
        data[offset : offset + 3] = b"\x00\x00\xff"
    image = SimpleNamespace(
        get_pixels=lambda: bytes(data),
        get_rowstride=lambda: width * 3,
        get_n_channels=lambda: 3,
    )
    assert dock_marker_points(image, (0, 0, width, height)) == (
        dock if dock_present else set()
    )


def test_native_probe_closes_cover_and_scene_even_when_a_wait_fails():
    from tools.linux.visual.probes.native_services_probe import Probe

    events = []

    def wait(name, timeout):
        assert timeout == 3
        events.append(f"wait-{name}")
        if name == "scene":
            raise subprocess.TimeoutExpired(name, timeout)

    probe = Probe.__new__(Probe)
    probe.child = SimpleNamespace(
        terminate=lambda: events.append("terminate-cover"),
        wait=lambda timeout: wait("cover", timeout),
    )
    probe.scene_child = SimpleNamespace(
        terminate=lambda: events.append("terminate-scene"),
        wait=lambda timeout: wait("scene", timeout),
    )
    with pytest.raises(subprocess.TimeoutExpired):
        probe.close()
    assert events == ["terminate-cover", "terminate-scene", "wait-scene", "wait-cover"]


def test_native_probe_quits_and_reports_failure_when_cleanup_times_out(
    monkeypatch, tmp_path, capsys
):
    import docking
    from docking.core.config import Config
    from tools.linux.visual.probes import native_services_probe as native

    async def completed():
        return

    callbacks = []
    app = SimpleNamespace(_start_runtime=lambda *_: None)

    def main():
        app._start_runtime(SimpleNamespace(_window=None), None, None)
        assert callbacks.pop()() is False

    app.main = main
    monkeypatch.setattr(docking, "app", app, raising=False)
    monkeypatch.setitem(sys.modules, "docking.app", app)
    monkeypatch.setattr(sys, "argv", [native.__file__, "bottom"])
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(Config, "save", lambda _: None)
    monkeypatch.setattr(native.GdkPixbuf.Pixbuf, "new", MagicMock())
    probe = SimpleNamespace(
        run=completed,
        close=MagicMock(side_effect=subprocess.TimeoutExpired("fixture", 3)),
    )
    monkeypatch.setattr(native, "Probe", lambda *_: probe)
    monkeypatch.setattr(
        native.GLib, "timeout_add", lambda _ms, callback: callbacks.append(callback)
    )
    quit_main = MagicMock()
    monkeypatch.setattr(native.Gtk, "main_quit", quit_main)

    with pytest.raises(SystemExit) as result:
        native.main()
    assert result.value.code == 1
    quit_main.assert_called_once_with()
    assert "TimeoutExpired" in capsys.readouterr().out


def test_native_panel_scene_is_not_shadowed_by_scene_client(tmp_path, monkeypatch):
    from tools.linux.visual.probes import native_services_probe as native

    monkeypatch.setitem(sys.modules, "native_services_probe", native)
    from tools.linux.visual.probes.phoc_panel_probe import PanelProbe

    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    image = SimpleNamespace(
        get_width=lambda: 48,
        get_height=lambda: 48,
        get_pixels=lambda: b"\x00\x00\xff" * 48 * 48,
        get_rowstride=lambda: 48 * 3,
        get_n_channels=lambda: 3,
    )

    async def capture(_phase):
        return image

    probe = PanelProbe(None, None, "bottom")
    probe.capture = capture
    task = probe.scene("before-panel", panel_present=False, expected_shift=0)
    with pytest.raises(StopIteration) as result:
        task.send(None)
    assert result.value.value == 0


@pytest.mark.parametrize("compositor", ["phoc", "phosh", "weston"])
@pytest.mark.parametrize("layer_shell", [False, True])
def test_additional_adapter_capabilities(compositor, layer_shell):
    if not shutil.which("jq"):
        pytest.skip("adapter capability rendering requires jq")
    root = Path(__file__).resolve().parents[1]
    probe = json.dumps(
        {"gtk_display_is_wayland": True, "layer_shell_supported": layer_shell}
    )
    script = f"""
export LAB_SCRIPTS="{root}/tools/linux/visual"
source "{root}/tools/linux/visual/adapters/{compositor}.sh"
session_probe_json() {{ printf '%s\\n' '{probe}'; }}
export LAB_PANEL_HEIGHT=36
adapter_capabilities
"""
    caps = json.loads(subprocess.check_output(["bash", "-c", script], text=True))
    assert caps["gtk_display_is_wayland"] is True
    assert caps["layer_shell_supported"] is layer_shell
    assert caps["native_geometry"] is False
    assert caps["pointer"] is False  # actual GTK delivery sets this later
    if compositor in {"phoc", "phosh"}:
        assert caps["expected_backend"] == "wayland-layer-shell"
        assert caps["placement"] is layer_shell
        if compositor == "phoc":
            assert caps["panel"] is True
        assert caps["screenshot_method"] == "grim"
    else:
        assert caps["expected_backend"] == "reduced"
        assert caps["placement"] is False
        assert "placement" in caps["unsupported_reasons"]
        assert caps["screenshot_method"] == "private-xvfb"


@pytest.mark.parametrize(
    ("private_previews", "color"),
    [
        (True, b"\xff\x00\x00"),
        (True, b"\x00\x00\x00"),
        (True, b"\x00\xff\x00"),
        (False, b"\x00\x00\x00"),
    ],
)
@pytest.mark.parametrize("minimize_delivered", [False, True])
def test_foreign_service_probe_requires_actual_fixture_pixels(
    tmp_path, monkeypatch, color, minimize_delivered, private_previews
):
    from tools.linux.visual.probes import foreign_services_probe as probe

    monkeypatch.setenv("LAB_EXPECTED_BACKEND", "wayland-layer-shell")
    monkeypatch.setenv("LAB_COMPOSITOR", "phoc")
    monkeypatch.setenv(
        "DOCKING_PHOC_PRIVATE_PREVIEWS", "1" if private_previews else "0"
    )
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    monkeypatch.setattr(probe.subprocess, "Popen", lambda *_: SimpleNamespace())
    ticks = itertools.count()
    monkeypatch.setattr(probe.time, "monotonic", lambda: next(ticks))
    row = SimpleNamespace(
        id=1,
        title="Native backend fixture",
        geometry=None,
        active=False,
        visible=True,
        minimized=False,
        can_preview=private_previews,
        can_minimize=True,
    )
    rows = [row]

    def activate(_id):
        row.active, row.visible, row.minimized = True, True, False
        return SimpleNamespace(succeeded=True)

    def minimize(_app):
        if minimize_delivered:
            row.active, row.visible, row.minimized = False, False, True
        return SimpleNamespace(succeeded=True)

    def close(_id):
        rows.clear()
        return SimpleNamespace(succeeded=True)

    image = SimpleNamespace(
        get_width=lambda: 240,
        get_height=lambda: 135,
        get_rowstride=lambda: 720,
        get_n_channels=lambda: 3,
        get_pixels=lambda: color * 240 * 135,
        savev=lambda *_: None,
    )
    preview_requests = []

    def capture(*args, **kwargs):
        preview_requests.append(args)
        return SimpleNamespace(image=image)

    idle_values = iter([1.0, 1.1, 1.4])
    backend = SimpleNamespace(
        name="wayland-layer-shell",
        capabilities=SimpleNamespace(
            tracks_windows=True,
            supports_activate=True,
            supports_overlap_active=False,
            supports_overlap_any=False,
        ),
        windows=SimpleNamespace(
            list_all_windows=lambda: rows,
            activate=activate,
            minimize_all=minimize,
            close=close,
        ),
        idle=SimpleNamespace(idle_seconds=lambda: next(idle_values)),
        previews=SimpleNamespace(capture=capture),
    )
    task = probe.ForeignServicesProbe(None, backend, "bottom").run()
    try:
        while True:
            task.send(None)
    except StopIteration:
        assert not private_previews or color == b"\xff\x00\x00"
        report = json.loads((tmp_path / "foreign-services.json").read_text())
        assert report["preview_pixels"] is private_previews
        assert bool(preview_requests) is private_previews
        assert report["private_previews_opted_in"] is private_previews
        assert report["window_geometry"] is None
        assert report["minimize_restore"] is minimize_delivered
        assert (
            "minimize_restore" in report["unsupported_reasons"]
        ) is not minimize_delivered
        assert report["close"] is True
    except AssertionError:
        assert private_previews and color != b"\xff\x00\x00"
        assert not (tmp_path / "foreign-services.json").exists()
