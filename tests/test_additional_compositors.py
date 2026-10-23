"""Ensure new lanes report honest backend support and require service evidence."""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("dock_present", [False, True])
def test_native_pixel_marker_excludes_fixture_titlebar_icon(dock_present):
    from tools.visual_compositor.probes.native_services_probe import dock_marker_points

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
export LAB_SCRIPTS="{root}/tools/visual_compositor"
source "{root}/tools/visual_compositor/adapters/{compositor}.sh"
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
    from tools.visual_compositor.probes import foreign_services_probe as probe

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
