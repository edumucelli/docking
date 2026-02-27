"""Tests for the ambient sound applet."""

from unittest.mock import MagicMock, patch

from docking.applets.ambient import (
    ALL_SOUNDS,
    DEFAULT_SOUND,
    DEFAULT_VOLUME,
    VOLUME_STEP,
    AmbientApplet,
)


def _make_applet() -> AmbientApplet:
    """Create applet with mocked GStreamer."""
    with patch("docking.applets.ambient.Gst"):
        return AmbientApplet(48)


class TestAmbientApplet:
    def test_creates_with_icon(self):
        applet = _make_applet()
        assert applet.item.icon is not None

    def test_default_state(self):
        applet = _make_applet()
        assert applet._current == DEFAULT_SOUND
        assert applet._volume == DEFAULT_VOLUME
        assert applet._playing is False

    def test_tooltip_when_stopped(self):
        applet = _make_applet()
        assert applet.item.name == "Ambient"

    def test_tooltip_when_playing(self):
        applet = _make_applet()
        applet._playing = True
        applet._current = "fireplace"
        applet._update_tooltip()
        assert "Fireplace" in applet.item.name
        assert "Playing" in applet.item.name

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = _make_applet()
            pixbuf = applet.create_icon(size=size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_menu_has_all_sounds(self):
        applet = _make_applet()
        labels = [mi.get_label() for mi in applet.get_menu_items()]
        for sound in ALL_SOUNDS:
            assert sound.label in labels

    def test_scroll_up_increases_volume(self):
        applet = _make_applet()
        before = applet._volume
        applet.on_scroll(direction_up=True)
        assert applet._volume == before + VOLUME_STEP

    def test_scroll_down_decreases_volume(self):
        applet = _make_applet()
        before = applet._volume
        applet.on_scroll(direction_up=False)
        assert applet._volume == before - VOLUME_STEP

    def test_volume_clamps_at_max(self):
        applet = _make_applet()
        applet._volume = 1.0
        applet.on_scroll(direction_up=True)
        assert applet._volume == 1.0

    def test_volume_clamps_at_min(self):
        applet = _make_applet()
        applet._volume = 0.0
        applet.on_scroll(direction_up=False)
        assert applet._volume == 0.0

    def test_click_toggles_play(self):
        applet = _make_applet()
        applet._start_playback = MagicMock()
        applet._stop_playback = MagicMock()
        applet.on_clicked()
        applet._start_playback.assert_called_once()

    def test_click_stops_when_playing(self):
        applet = _make_applet()
        applet._playing = True
        applet._stop_playback = MagicMock()
        applet.on_clicked()
        applet._stop_playback.assert_called_once()
