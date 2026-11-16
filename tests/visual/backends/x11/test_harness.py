"""Reject blank, misplaced and incomplete screenshots in the BSD visual lab."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.visual.backends.x11 import run
from tests.visual.backends.x11.pixels import Frame, placement
from tests.visual.backends.x11.run import private_environment, report


def frame(
    width=200, height=160, rect=None, channels=3, padding=0, color=(30, 190, 220)
):
    stride = width * channels + padding
    pixels = bytearray(stride * height)
    if rect:
        left, top, right, bottom = rect
        for y in range(top, bottom):
            for x in range(left, right):
                start = y * stride + x * channels
                pixels[start : start + 3] = bytes(color)
    return Frame(width, height, channels, stride, bytes(pixels))


@pytest.mark.parametrize("channels,padding", [(3, 0), (3, 2), (4, 4)])
@pytest.mark.parametrize("color", [(30, 190, 220), (35, 224, 255)])
def test_markers_respect_channels_row_padding_and_hover_brightness(
    channels, padding, color
):
    image = frame(
        rect=(80, 120, 120, 150), channels=channels, padding=padding, color=color
    )
    assert placement(image, "bottom")["count"] == 1200
    assert placement(image, "bottom")["distance"] == 10


def test_window_manager_titlebar_blue_is_not_a_dock_marker():
    image = frame(rect=(80, 120, 120, 150))
    pixels = bytearray(image.pixels)
    for x in range(image.width):
        pixels[x * 3 : x * 3 + 3] = bytes((60, 180, 235))
    decorated = Frame(
        image.width, image.height, image.channels, image.stride, bytes(pixels)
    )
    assert placement(decorated, "bottom") == placement(image, "bottom")


def test_native_panel_icon_copies_and_unrelated_cyan_do_not_move_the_dock():
    image = frame(rect=(80, 120, 120, 150))
    pixels = bytearray(image.pixels)
    for y in range(16):
        for x in range(16):
            offset = y * image.stride + x * 3
            pixels[offset : offset + 3] = bytes((30, 190, 220))
    for y in range(40, 80):
        for x in range(40):
            offset = y * image.stride + x * 3
            pixels[offset : offset + 3] = bytes((0, 190, 220))
    decorated = Frame(
        image.width, image.height, image.channels, image.stride, bytes(pixels)
    )
    assert placement(decorated, "bottom") == placement(image, "bottom")


def test_vertical_placement_centers_inside_the_desktops_observed_workarea():
    image = frame(height=180, rect=(10, 95, 50, 125))
    with pytest.raises(AssertionError, match="misplaced"):
        placement(image, "left")
    observed = Frame(
        image.width,
        image.height,
        image.channels,
        image.stride,
        image.pixels,
        (0, 40, 200, 140),
    )
    assert placement(observed, "left")["distance"] == 10


@pytest.mark.parametrize(
    "rect", [None, (80, 120, 90, 130), (0, 0, 40, 30), (80, 20, 120, 50)]
)
def test_blank_incomplete_off_center_or_wrong_edge_fails(rect):
    with pytest.raises(AssertionError):
        placement(frame(rect=rect), "bottom")


def test_tooltip_comparison_excludes_changes_within_the_dock():
    empty = frame()
    dock = frame(rect=(80, 120, 120, 150))
    assert empty.changed_above(dock, 100) == 0
    assert empty.changed_above(dock, 160) == 1200


def test_report_retains_failures_and_escapes_error_text(tmp_path):
    report(
        tmp_path,
        {"os": "test"},
        [{"case": "startup", "status": "fail", "detail": "<failed>"}],
    )
    html = (tmp_path / "index.html").read_text()
    assert "&lt;failed&gt;" in html
    assert '"status": "fail"' in (tmp_path / "results.json").read_text()


def test_service_environment_does_not_reuse_host_displays_or_settings(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_SESSION_DESKTOP", "host-desktop")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/host/config")
    env = private_environment(tmp_path)
    assert "DISPLAY" not in env
    assert "WAYLAND_DISPLAY" not in env
    assert "XDG_SESSION_DESKTOP" not in env
    assert env["XDG_CONFIG_HOME"] == str(tmp_path / "config")
    assert (tmp_path / "config").stat().st_mode & 0o777 == 0o700


def test_gap_scenario_can_run_without_other_placement_cases(monkeypatch):
    @contextmanager
    def dock_case(*args, **kwargs):
        yield None

    monkeypatch.setattr(run, "dock_case", dock_case)
    monkeypatch.setattr(
        run,
        "ready",
        lambda *args: SimpleNamespace(get_geometry=lambda: (0, 0, 200, 160)),
    )
    observer = SimpleNamespace(
        pointer=MagicMock(),
        settled=lambda name: frame(rect=(80, 104, 120, 134)),
    )
    results = []

    def record(name, check):
        if name == "placement-bottom-gap":
            results.append(check())

    run.run_checks(observer, {}, record)
    assert results[0]["pixels"]["distance"] == 26
