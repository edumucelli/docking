"""Tests for the brightness applet."""

from unittest.mock import MagicMock, patch

import docking.applets.brightness.state as brightness_state
from docking.applets.brightness import (
    STEP,
    Backend,
    BrightnessApplet,
    brightness_icon_name,
    detect_output,
    get_brightness,
)


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


def _make_applet(brightness: float = 0.8) -> BrightnessApplet:
    backend = Backend(output="HDMI-1")
    with (
        patch("docking.applets.brightness.applet.detect_output", return_value=backend),
        patch(
            "docking.applets.brightness.applet.get_brightness",
            return_value=brightness,
        ),
    ):
        return BrightnessApplet(48)


class TestBrightnessApplet:
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

    def test_poll_result_updates_on_change(self):
        applet = _make_applet(brightness=0.5)
        applet.refresh_presentation = MagicMock()
        applet._on_poll_result(0.8)
        assert applet._brightness == 0.8
        applet.refresh_presentation.assert_called_once()

    def test_poll_result_ignores_none(self):
        applet = _make_applet(brightness=0.5)
        applet.refresh_presentation = MagicMock()
        applet._on_poll_result(None)
        assert applet._brightness == 0.5
        applet.refresh_presentation.assert_not_called()

    def test_poll_result_ignores_small_change(self):
        applet = _make_applet(brightness=0.5)
        applet.refresh_presentation = MagicMock()
        applet._on_poll_result(0.505)
        applet.refresh_presentation.assert_not_called()
