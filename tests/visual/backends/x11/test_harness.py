"""Reject blank, misplaced and incomplete screenshots in the BSD visual lab."""

import errno
import json
import signal
import subprocess
from contextlib import contextmanager
from dataclasses import replace
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


def test_compositor_readiness_dispatches_pending_window_manager_events():
    ready = False

    def dispatch():
        nonlocal ready
        ready = True

    observer = run.Observer.__new__(run.Observer)
    observer.windows = dispatch
    observer.Gdk = SimpleNamespace(
        Screen=SimpleNamespace(
            get_default=lambda: SimpleNamespace(is_composited=lambda: ready)
        )
    )
    assert observer.composited()


@pytest.mark.parametrize("timeout", [False, True])
def test_private_helpers_are_stopped_after_their_launcher_exits(monkeypatch, timeout):
    kill = MagicMock()
    monkeypatch.setattr(run.os, "killpg", kill)
    child = MagicMock(pid=12345)
    child.poll.return_value = 0
    if timeout:
        child.wait.side_effect = [subprocess.TimeoutExpired("fixture", 5), 0]
    run.stop_process_group(child)
    assert [call.args for call in kill.call_args_list] == [
        (12345, signal.SIGTERM),
        (12345, signal.SIGKILL),
    ]


@pytest.mark.parametrize(
    "error_code,persistent",
    [(errno.ENOTEMPTY, False), (errno.ENOTEMPTY, True), (errno.EACCES, True)],
)
def test_session_cleanup_retries_only_transient_directory_writes(
    monkeypatch, tmp_path, error_code, persistent
):
    cleanup = run.tempfile.TemporaryDirectory.cleanup
    attempts = []

    def raced_cleanup(directory):
        attempts.append(directory.name)
        if persistent or len(attempts) == 1:
            raise OSError(error_code, "Late desktop write")
        return cleanup(directory)

    monkeypatch.setattr(run.tempfile.TemporaryDirectory, "cleanup", raced_cleanup)
    monkeypatch.setattr(run.time, "sleep", lambda _: None)
    if persistent:
        with pytest.raises(OSError) as error, run.SessionDirectory(dir=tmp_path):
            pass
        assert error.value.errno == error_code
        if error_code == errno.ENOTEMPTY:
            assert 1 < len(attempts) < 100
        else:
            assert len(attempts) == 1
    else:
        with run.SessionDirectory(dir=tmp_path) as directory:
            path = run.Path(directory)
            assert path.is_dir()
        assert len(attempts) == 2
        assert not path.exists()


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
    monkeypatch.setenv("HOME", "/host/home")
    env = private_environment(tmp_path)
    assert "DISPLAY" not in env
    assert "WAYLAND_DISPLAY" not in env
    assert "XDG_SESSION_DESKTOP" not in env
    assert env["XDG_CONFIG_HOME"] == str(tmp_path / "config")
    assert (tmp_path / "config").stat().st_mode & 0o777 == 0o700
    assert env["HOME"] == str(tmp_path / "home")
    assert (tmp_path / "home").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("capture_fails", [False, True])
def test_setup_failure_keeps_original_error_and_attempts_native_capture(
    monkeypatch, tmp_path, capture_fails
):
    observer = MagicMock(versions={})
    observer.window_manager.side_effect = RuntimeError("window manager failed")
    if capture_fails:
        observer.capture.side_effect = RuntimeError("capture unavailable")
    monkeypatch.setattr(run, "Observer", lambda *args: observer)
    monkeypatch.setattr(run, "environment_metadata", lambda args: {})
    args = SimpleNamespace(output=tmp_path, dock_command=None, case=[])

    assert run.observe(args) == 1

    observer.capture.assert_called_once_with("observer-setup-failure")
    observer.close.assert_called_once()
    results = json.loads((tmp_path / "results.json").read_text())["results"]
    assert len(results) == 1
    assert "window manager failed" in results[0]["detail"]
    assert ("capture_error" in results[0]) is capture_fails


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


@pytest.mark.parametrize("gap_applied", [True, False])
def test_gap_pair_uses_captured_pixels_when_panel_workarea_updates_late(
    monkeypatch, gap_applied
):
    @contextmanager
    def dock_case(*args, **kwargs):
        yield None

    monkeypatch.setattr(run, "dock_case", dock_case)
    monkeypatch.setattr(
        run,
        "ready",
        lambda *args: SimpleNamespace(get_geometry=lambda: (0, 0, 200, 160)),
    )
    baseline = frame(rect=(80, 115, 120, 145))
    offset = 16 if gap_applied else 0
    shifted = replace(
        frame(rect=(80, 115 - offset, 120, 145 - offset)),
        workarea=(0, 0, 200, 150 if gap_applied else 176),
    )
    observer = SimpleNamespace(
        pointer=MagicMock(),
        settled=lambda name: shifted if name.endswith("-gap") else baseline,
    )

    def record(name, check):
        if name in {"placement-bottom", "placement-bottom-gap"}:
            check()

    if gap_applied:
        run.run_checks(observer, {}, record)
    else:
        with pytest.raises(AssertionError):
            run.run_checks(observer, {}, record)
