"""Tests for the brightness applet."""

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gi import repository as gi_repository

import docking.applets.brightness.state as brightness_state
from docking.applets.brightness.applet import BrightnessApplet
from docking.applets.brightness.state import (
    STEP,
    Backend,
    brightness_icon_name,
    detect_output,
    get_brightness,
)
from docking.core.config import Config


class _ImmediateWorker:
    def __init__(self, **_kwargs) -> None:
        pass

    def run(self, *, fn, on_result=None, on_error=None, **_kwargs) -> None:
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return
        if on_result is not None:
            on_result(result)

    def run_guarded(self, *, fn, on_result=None, on_error=None, **_kwargs) -> bool:
        self.run(fn=fn, on_result=on_result, on_error=on_error)
        return True


class TestBrightnessIconName:
    def test_low(self):
        assert "low" in brightness_icon_name(brightness=0.2)

    def test_medium(self):
        assert "medium" in brightness_icon_name(brightness=0.5)

    def test_high(self):
        name = brightness_icon_name(brightness=0.9)
        assert "low" not in name and "medium" not in name


class TestDetectOutput:
    def test_parses_xrandr_listmonitors(self, monkeypatch):
        output = "Monitors: 1\n 0: +*HDMI-1 1920/480x1080/270+0+0  HDMI-1\n"
        monkeypatch.setattr(brightness_state, "_run", lambda cmd: output)
        monkeypatch.setattr(brightness_state, "_find_sysfs_backlight", lambda: None)
        monkeypatch.setattr(brightness_state.shutil, "which", lambda _cmd: None)
        result = detect_output()
        assert result == Backend(output="HDMI-1", sysfs=None, brightnessctl=None)

    def test_includes_sysfs_when_available(self, monkeypatch, tmp_path):
        output = "Monitors: 1\n 0: +*eDP-1 1920/300x1080/170+0+0  eDP-1\n"
        monkeypatch.setattr(brightness_state, "_run", lambda cmd: output)
        monkeypatch.setattr(brightness_state, "_find_sysfs_backlight", lambda: tmp_path)
        monkeypatch.setattr(brightness_state.shutil, "which", lambda _cmd: None)
        result = detect_output()
        assert result == Backend(output="eDP-1", sysfs=tmp_path, brightnessctl=None)

    def test_includes_brightnessctl_when_available(self, monkeypatch):
        output = "Monitors: 1\n 0: +*eDP-1 1920/300x1080/170+0+0  eDP-1\n"
        monkeypatch.setattr(brightness_state, "_run", lambda cmd: output)
        monkeypatch.setattr(brightness_state, "_find_sysfs_backlight", lambda: None)
        monkeypatch.setattr(
            brightness_state.shutil, "which", lambda cmd: "/usr/bin/brightnessctl"
        )
        result = detect_output()
        assert result == Backend(
            output="eDP-1", sysfs=None, brightnessctl="/usr/bin/brightnessctl"
        )

    def test_returns_none_on_failure(self, monkeypatch):
        monkeypatch.setattr(brightness_state, "_run", lambda cmd: None)
        assert detect_output() is None

    def test_returns_none_when_line_has_too_few_parts(self, monkeypatch):
        monkeypatch.setattr(brightness_state, "_run", lambda cmd: "Monitors: 1\n 0:\n")
        assert detect_output() is None


class TestGetBrightness:
    def test_prefers_sysfs_when_available_on_wayland(self, monkeypatch, tmp_path):
        monkeypatch.setattr(brightness_state, "is_wayland_session", lambda: True)
        (tmp_path / "brightness").write_text("150\n")
        (tmp_path / "max_brightness").write_text("200\n")
        backend = Backend(output="eDP-1", sysfs=tmp_path)
        assert get_brightness(backend=backend) == 0.75

    def test_prefers_xrandr_when_available_on_x11(self, monkeypatch, tmp_path):
        monkeypatch.setattr(brightness_state, "is_wayland_session", lambda: False)
        (tmp_path / "brightness").write_text("150\n")
        (tmp_path / "max_brightness").write_text("200\n")
        output = (
            "HDMI-1 connected primary 1920x1080\n"
            "\tBrightness: 0.40\n"
            "\tGamma: 1.0:1.0:1.0\n"
        )
        monkeypatch.setattr(brightness_state, "_run", lambda cmd: output)
        backend = Backend(output="HDMI-1", sysfs=tmp_path)
        assert get_brightness(backend=backend) == 0.40

    def test_sysfs_handles_read_error(self, monkeypatch, tmp_path):
        # Given - missing files
        monkeypatch.setattr(brightness_state, "is_wayland_session", lambda: True)
        backend = Backend(output="eDP-1", sysfs=tmp_path)
        # When / Then
        assert get_brightness(backend=backend) is None

    def test_falls_back_to_xrandr_without_sysfs(self, monkeypatch):
        # Given
        output = (
            "HDMI-1 connected primary 1920x1080\n"
            "\tBrightness: 0.75\n"
            "\tGamma: 1.0:1.0:1.0\n"
        )
        monkeypatch.setattr(brightness_state, "_run", lambda cmd: output)
        backend = Backend(output="HDMI-1", sysfs=None)
        # When / Then
        assert get_brightness(backend=backend) == 0.75

    def test_falls_back_to_sysfs_on_x11_when_xrandr_missing(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(brightness_state, "is_wayland_session", lambda: False)
        monkeypatch.setattr(brightness_state, "_run", lambda cmd: None)
        (tmp_path / "brightness").write_text("150\n")
        (tmp_path / "max_brightness").write_text("200\n")
        backend = Backend(output="eDP-1", sysfs=tmp_path)
        assert get_brightness(backend=backend) == 0.75

    def test_xrandr_returns_none_on_failure(self, monkeypatch):
        monkeypatch.setattr(brightness_state, "_run", lambda cmd: None)
        assert get_brightness(backend=Backend(output="HDMI-1")) is None

    def test_xrandr_returns_none_when_output_not_found(self, monkeypatch):
        output = "DP-1 connected\n\tBrightness: 0.55\n"
        monkeypatch.setattr(brightness_state, "_run", lambda cmd: output)
        assert get_brightness(backend=Backend(output="HDMI-1")) is None


class TestFindSysfsBacklight:
    def test_finds_backlight_dir(self, monkeypatch, tmp_path):
        # Given
        bl = tmp_path / "intel_backlight"
        bl.mkdir()
        (bl / "brightness").write_text("100\n")
        (bl / "max_brightness").write_text("200\n")
        monkeypatch.setattr(brightness_state, "_BACKLIGHT_DIR", tmp_path)
        # When / Then
        assert brightness_state._find_sysfs_backlight() == bl

    def test_returns_none_when_no_backlight(self, monkeypatch, tmp_path):
        monkeypatch.setattr(brightness_state, "_BACKLIGHT_DIR", tmp_path)
        assert brightness_state._find_sysfs_backlight() is None

    def test_returns_none_when_dir_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            brightness_state, "_BACKLIGHT_DIR", tmp_path / "nonexistent"
        )
        assert brightness_state._find_sysfs_backlight() is None


class TestBrightnessStateHelpers:
    def test_run_helper(self, monkeypatch):
        class _Proc:
            def __init__(self, code=0, out=""):
                self.returncode = code
                self.stdout = out

        monkeypatch.setattr(
            brightness_state.subprocess,
            "run",
            lambda *args, **kwargs: _Proc(0, "ok"),
        )
        assert brightness_state._run(["xrandr"]) == "ok"

        monkeypatch.setattr(
            brightness_state.subprocess,
            "run",
            lambda *args, **kwargs: _Proc(1, "fail"),
        )
        assert brightness_state._run(["xrandr"]) is None

        def fail_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="xrandr", timeout=1)

        monkeypatch.setattr(brightness_state.subprocess, "run", fail_run)
        assert brightness_state._run(["xrandr"]) is None

    def test_set_brightness_clamps(self, monkeypatch):
        calls: list[list[str]] = []
        monkeypatch.setattr(
            brightness_state, "_run", lambda cmd: calls.append(cmd) or ""
        )
        brightness_state.set_brightness(Backend(output="HDMI-1"), -10.0)
        brightness_state.set_brightness(Backend(output="HDMI-1"), 10.0)
        assert calls[0][-1] == "0.10"
        assert calls[1][-1] == "1.00"

    def test_set_brightness_prefers_brightnessctl(self, monkeypatch):
        monkeypatch.setattr(brightness_state, "is_wayland_session", lambda: True)
        calls: list[list[str]] = []
        monkeypatch.setattr(
            brightness_state, "_run", lambda cmd: calls.append(cmd) or ""
        )
        brightness_state.set_brightness(
            Backend(output="eDP-1", brightnessctl="/usr/bin/brightnessctl"),
            0.75,
        )
        assert calls == [["/usr/bin/brightnessctl", "set", "75%"]]

    def test_set_brightness_prefers_xrandr_on_x11_even_with_sysfs(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(brightness_state, "is_wayland_session", lambda: False)
        calls: list[list[str]] = []
        monkeypatch.setattr(
            brightness_state, "_run", lambda cmd: calls.append(cmd) or ""
        )
        (tmp_path / "max_brightness").write_text("200\n")
        (tmp_path / "brightness").write_text("100\n")

        brightness_state.set_brightness(Backend(output="eDP-1", sysfs=tmp_path), 0.75)

        assert calls == [["xrandr", "--output", "eDP-1", "--brightness", "0.75"]]
        assert (tmp_path / "brightness").read_text() == "100\n"

    def test_set_brightness_uses_sysfs_when_available(self, tmp_path):
        with patch.object(brightness_state, "is_wayland_session", return_value=True):
            (tmp_path / "max_brightness").write_text("200\n")
            (tmp_path / "brightness").write_text("100\n")

            brightness_state.set_brightness(
                Backend(output="eDP-1", sysfs=tmp_path), 0.75
            )

        assert (tmp_path / "brightness").read_text() == "150\n"

    def test_set_brightness_sysfs_clamps(self, tmp_path):
        with patch.object(brightness_state, "is_wayland_session", return_value=True):
            (tmp_path / "max_brightness").write_text("200\n")
            (tmp_path / "brightness").write_text("100\n")

            brightness_state.set_brightness(
                Backend(output="eDP-1", sysfs=tmp_path), -1.0
            )

        assert (tmp_path / "brightness").read_text() == "20\n"

    def test_get_brightness_calls_platform_wayland_helper(self, monkeypatch, tmp_path):
        (tmp_path / "max_brightness").write_text("200\n")
        (tmp_path / "brightness").write_text("120\n")
        probe = MagicMock(return_value=True)
        monkeypatch.setattr(brightness_state, "is_wayland_session", probe)

        assert get_brightness(backend=Backend(output="eDP-1", sysfs=tmp_path)) == 0.6
        probe.assert_called_once_with()


def _make_applet(brightness: float = 0.8) -> BrightnessApplet:
    backend = Backend(output="HDMI-1")
    with (
        patch("docking.applets.brightness.applet.BackgroundWorker", _ImmediateWorker),
        patch("docking.applets.brightness.applet.detect_output", return_value=backend),
        patch(
            "docking.applets.brightness.applet.get_brightness",
            return_value=brightness,
        ),
    ):
        return BrightnessApplet(48, config=Config())


class TestBrightnessApplet:
    def _fake_gtk(self, monkeypatch):
        class _FakeCheckMenuItem:
            def __init__(self, label: str = "") -> None:
                self._label = label
                self._active = False
                self._signals: dict[str, list[object]] = {}

            def get_label(self) -> str:
                return self._label

            def set_active(self, active: bool) -> None:
                self._active = active

            def get_active(self):
                return self._active

            def connect(self, signal: str, callback) -> None:
                self._signals.setdefault(signal, []).append(callback)

        monkeypatch.setattr(
            gi_repository,
            "Gtk",
            SimpleNamespace(CheckMenuItem=_FakeCheckMenuItem),
        )

    def test_creates_with_icon(self):
        applet = _make_applet()
        assert applet.item.icon is not None

    def test_tooltip_shows_percentage(self):
        applet = _make_applet(brightness=0.75)
        assert "75%" in applet.item.name

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = _make_applet()
            pixbuf = applet.create_icon(size=size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_scroll_up_increases(self):
        applet = _make_applet(brightness=0.5)
        with patch("docking.applets.brightness.applet.set_brightness") as mock_set:
            applet.on_scroll(direction_up=True)
        expected = 0.5 + STEP
        mock_set.assert_called_once()
        assert abs(mock_set.call_args[1]["value"] - expected) < 0.001

    def test_scroll_down_decreases(self):
        applet = _make_applet(brightness=0.5)
        with patch("docking.applets.brightness.applet.set_brightness") as mock_set:
            applet.on_scroll(direction_up=False)
        expected = 0.5 - STEP
        mock_set.assert_called_once()
        assert abs(mock_set.call_args[1]["value"] - expected) < 0.001

    def test_scroll_clamps_at_min(self):
        applet = _make_applet(brightness=0.1)
        with patch("docking.applets.brightness.applet.set_brightness") as mock_set:
            applet.on_scroll(direction_up=False)
        assert mock_set.call_args[1]["value"] >= 0.1

    def test_click_resets_to_full(self):
        applet = _make_applet(brightness=0.5)
        with patch("docking.applets.brightness.applet.set_brightness") as mock_set:
            applet.on_clicked()
        mock_set.assert_called_once_with(backend=applet._backend, value=1.0)
        assert applet._brightness == 1.0

    def test_no_backend_is_safe(self):
        with patch(
            "docking.applets.brightness.applet.detect_output", return_value=None
        ):
            applet = BrightnessApplet(48, config=Config())
        applet.on_scroll(direction_up=True)
        applet.on_clicked()

    def test_start_stop_menu_and_toggle(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        applet = _make_applet(brightness=0.5)
        removed: list[int] = []
        monkeypatch.setattr(
            brightness_state,
            "STEP",
            STEP,
        )
        monkeypatch.setattr(
            "docking.applets.brightness.applet.GLib.timeout_add_seconds",
            lambda sec, cb: 11,
        )
        monkeypatch.setattr(
            "docking.applets.brightness.applet.GLib.source_remove",
            lambda source_id: removed.append(source_id),
        )
        applet.start(notify=lambda: None)
        assert applet._timer_id == 11
        applet.stop()
        assert applet._timer_id == 0
        assert removed == [11]

        labels = [item.get_label() for item in applet.get_menu_items()]
        assert "Show Level" in labels

        class _Widget:
            def __init__(self, active: bool):
                self._active = active

            def get_active(self):
                return self._active

        applet.present = MagicMock()
        applet._on_toggle_level(_Widget(active=True))
        assert applet._show_level is True
        applet.present.assert_called_once()

    def test_poll_and_tick_branches(self, monkeypatch):
        applet = _make_applet(brightness=0.5)
        applet._backend = None
        assert applet._tick() is True
        applet._poll()

        backend = Backend(output="HDMI-1")
        applet._backend = backend
        monkeypatch.setattr(
            "docking.applets.brightness.applet.get_brightness", lambda backend: None
        )
        applet._poll()
        assert applet._brightness == 0.5

        seen: list[float | None] = []
        monkeypatch.setattr(
            applet, "_on_poll_result", lambda val: seen.append(val) or False
        )
        monkeypatch.setattr(
            "docking.applets.brightness.applet.get_brightness", lambda backend: 0.8
        )
        assert applet._tick() is True
        assert seen == [0.8]

    def test_poll_result_updates_on_change(self):
        applet = _make_applet(brightness=0.5)
        applet.present = MagicMock()
        applet._on_poll_result(0.8)
        assert applet._brightness == 0.8
        applet.present.assert_called_once()

    def test_poll_result_ignores_none(self):
        applet = _make_applet(brightness=0.5)
        applet.present = MagicMock()
        applet._on_poll_result(None)
        assert applet._brightness == 0.5
        applet.present.assert_not_called()

    def test_poll_result_ignores_small_change(self):
        applet = _make_applet(brightness=0.5)
        applet.present = MagicMock()
        applet._on_poll_result(0.505)
        applet.present.assert_not_called()
