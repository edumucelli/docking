"""Tests for the ambient sound applet."""

from unittest.mock import MagicMock, patch

from docking.applets.ambient import (
    ALL_SOUNDS,
    DEFAULT_SOUND,
    DEFAULT_VOLUME,
    VOLUME_STEP,
    AmbientApplet,
)
from docking.applets.ambient.applet import SOUNDS_DIR


def _make_applet() -> AmbientApplet:
    """Create applet with mocked GStreamer."""
    with patch("docking.applets.ambient.applet.Gst"):
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

    def test_sounds_dir_points_to_package_assets(self):
        assert SOUNDS_DIR.name == "sounds"
        assert SOUNDS_DIR.parent.name == "assets"
        assert SOUNDS_DIR.parent.parent.name == "docking"


class TestAmbientPlaybackBranches:
    def test_start_playback_noise_pipeline_sets_playing_state(self, monkeypatch):
        # Given
        applet = _make_applet()
        applet._current = "white-noise"
        pipeline = MagicMock()
        monkeypatch.setattr(
            "docking.applets.ambient.applet._build_noise_pipeline",
            lambda **_kwargs: pipeline,
        )

        # When
        applet._start_playback()

        # Then
        pipeline.set_state.assert_called_once()
        assert applet._playing is True

    def test_start_playback_file_missing_keeps_stopped(self, monkeypatch, tmp_path):
        # Given
        applet = _make_applet()
        applet._current = "birds"
        monkeypatch.setattr("docking.applets.ambient.applet.SOUNDS_DIR", tmp_path)

        # When
        applet._start_playback()

        # Then
        assert applet._pipeline is None
        assert applet._playing is False

    def test_stop_playback_cleans_pipeline_and_bus_watch(self):
        # Given
        applet = _make_applet()
        bus = MagicMock()
        pipeline = MagicMock()
        pipeline.get_bus.return_value = bus
        applet._pipeline = pipeline
        applet._bus_watching = True
        applet._playing = True

        # When
        applet._stop_playback()

        # Then
        bus.remove_signal_watch.assert_called_once()
        pipeline.set_state.assert_called_once()
        pipeline.get_state.assert_called_once()
        assert applet._pipeline is None
        assert applet._playing is False

    def test_apply_volume_uses_direct_volume_property(self):
        # Given
        applet = _make_applet()
        pipeline = MagicMock()
        pipeline.find_property.return_value = object()
        applet._pipeline = pipeline

        # When
        applet._apply_volume()

        # Then
        pipeline.set_property.assert_called_once_with("volume", applet._volume)

    def test_apply_volume_uses_named_volume_element_when_needed(self):
        # Given
        applet = _make_applet()
        pipeline = MagicMock()
        pipeline.find_property.return_value = None
        volume_element = MagicMock()
        pipeline.get_by_name.return_value = volume_element
        applet._pipeline = pipeline

        # When
        applet._apply_volume()

        # Then
        volume_element.set_property.assert_called_once_with("volume", applet._volume)
