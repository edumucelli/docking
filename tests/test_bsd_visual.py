"""Reject blank, misplaced and incomplete screenshots in the BSD visual lab."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tools.bsd_visual import run
from tools.bsd_visual.pixels import Frame, placement
from tools.bsd_visual.run import private_environment, report


def frame(width=200, height=160, rect=None, channels=3, padding=0):
    stride = width * channels + padding
    pixels = bytearray(stride * height)
    if rect:
        left, top, right, bottom = rect
        for y in range(top, bottom):
            for x in range(left, right):
                start = y * stride + x * channels
                pixels[start : start + 3] = bytes((30, 190, 220))
    return Frame(width, height, channels, stride, bytes(pixels))


@pytest.mark.parametrize("channels,padding", [(3, 0), (3, 2), (4, 4)])
def test_markers_respect_channels_and_row_padding(channels, padding):
    image = frame(rect=(80, 120, 120, 150), channels=channels, padding=padding)
    assert placement(image, "bottom")["count"] == 1200
    assert placement(image, "bottom")["distance"] == 10


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
