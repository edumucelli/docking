"""Tests for the brightness applet."""

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gi import repository as gi_repository

import docking.applets.brightness.state as brightness_state
from docking.applets.brightness import (
    STEP,
    Backend,
    BrightnessApplet,
    brightness_icon_name,
    detect_output,
    get_brightness,
)


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
        result = detect_output()
        assert result == Backend(output="HDMI-1")

    def test_returns_none_on_failure(self, monkeypatch):
        monkeypatch.setattr(brightness_state, "_run", lambda cmd: None)
        assert detect_output() is None

    def test_returns_none_when_line_has_too_few_parts(self, monkeypatch):
        monkeypatch.setattr(brightness_state, "_run", lambda cmd: "Monitors: 1\n 0:\n")
        assert detect_output() is None


class TestGetBrightness:
    def test_parses_xrandr_verbose(self, monkeypatch):
        output = (
            "HDMI-1 connected primary 1920x1080\n"
            "\tBrightness: 0.75\n"
            "\tGamma: 1.0:1.0:1.0\n"
        )
        monkeypatch.setattr(brightness_state, "_run", lambda cmd: output)
        assert get_brightness(backend=Backend(output="HDMI-1")) == 0.75

    def test_returns_none_on_failure(self, monkeypatch):
        monkeypatch.setattr(brightness_state, "_run", lambda cmd: None)
        assert get_brightness(backend=Backend(output="HDMI-1")) is None

    def test_returns_none_when_output_not_found(self, monkeypatch):
        output = "DP-1 connected\n\tBrightness: 0.55\n"
        monkeypatch.setattr(brightness_state, "_run", lambda cmd: output)
        assert get_brightness(backend=Backend(output="HDMI-1")) is None


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
        return BrightnessApplet(48)


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
            applet = BrightnessApplet(48)
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
