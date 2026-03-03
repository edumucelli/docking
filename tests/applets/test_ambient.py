"""Tests for the ambient sound applet."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import docking.applets.ambient.applet as ambient_mod
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


class TestAmbientLifecycleAndPipelines:
    def test_build_file_pipeline_returns_none_when_factory_missing(self, monkeypatch):
        monkeypatch.setattr(
            ambient_mod.Gst.ElementFactory, "make", lambda *_a, **_k: None
        )
        assert (
            ambient_mod._build_file_pipeline(path=Path("/tmp/missing.ogg"), volume=0.4)
            is None
        )

    def test_build_file_pipeline_sets_uri_and_volume(self, monkeypatch):
        playbin = MagicMock()
        monkeypatch.setattr(
            ambient_mod.Gst.ElementFactory, "make", lambda *_a, **_k: playbin
        )
        sound_path = Path("/tmp/sound.ogg")
        result = ambient_mod._build_file_pipeline(path=sound_path, volume=0.7)
        assert result is playbin
        playbin.set_property.assert_any_call("uri", sound_path.as_uri())
        playbin.set_property.assert_any_call("volume", 0.7)

    def test_build_noise_pipeline_delegates_to_parse_launch(self, monkeypatch):
        pipeline = MagicMock()
        monkeypatch.setattr(ambient_mod.Gst, "parse_launch", lambda _expr: pipeline)
        assert ambient_mod._build_noise_pipeline(wave=2, volume=0.3) is pipeline

    def test_start_and_stop_exercise_lifecycle_methods(self):
        applet = _make_applet()
        applet._stop_playback = MagicMock()
        applet.start(lambda: None)
        applet.stop()
        applet._stop_playback.assert_called_once()

    def test_select_sound_restarts_when_currently_playing(self):
        applet = _make_applet()
        target = ALL_SOUNDS[0].name
        applet._playing = True
        applet._stop_playback = MagicMock()
        applet._start_playback = MagicMock()
        applet._save = MagicMock()
        applet.refresh_presentation = MagicMock()

        applet._select_sound(name=target)

        applet._stop_playback.assert_called_once()
        applet._start_playback.assert_called_once()
        applet._save.assert_called_once()
        applet.refresh_presentation.assert_called_once()
        assert applet._current == target

    def test_start_playback_returns_when_sound_is_unknown(self):
        applet = _make_applet()
        applet._current = "unknown"
        applet._start_playback()
        assert applet._pipeline is None

    def test_start_playback_file_branch_sets_bus_watch(self, monkeypatch, tmp_path):
        applet = _make_applet()
        file_sound = next(s for s in ALL_SOUNDS if s.kind == "file")
        applet._current = file_sound.name
        sound_file = tmp_path / f"{file_sound.name}.ogg"
        sound_file.write_bytes(b"x")
        monkeypatch.setattr(ambient_mod, "SOUNDS_DIR", tmp_path)

        pipeline = MagicMock()
        bus = MagicMock()
        pipeline.get_bus.return_value = bus
        monkeypatch.setattr(ambient_mod, "_build_file_pipeline", lambda **_k: pipeline)

        applet._start_playback()

        bus.add_signal_watch.assert_called_once()
        bus.connect.assert_called_once()
        pipeline.set_state.assert_called_once_with(ambient_mod.Gst.State.PLAYING)
        assert applet._bus_watching is True

    def test_start_playback_warns_when_pipeline_creation_fails(self, monkeypatch):
        applet = _make_applet()
        applet._current = "white-noise"
        monkeypatch.setattr(ambient_mod, "_build_noise_pipeline", lambda **_k: None)

        applet._start_playback()

        assert applet._pipeline is None
        assert applet._playing is False

    def test_on_eos_seeks_to_start_when_pipeline_exists(self):
        applet = _make_applet()
        pipeline = MagicMock()
        applet._pipeline = pipeline

        applet._on_eos(None, None)

        pipeline.seek_simple.assert_called_once_with(
            ambient_mod.Gst.Format.TIME,
            ambient_mod.Gst.SeekFlags.FLUSH,
            0,
        )

    def test_apply_volume_no_pipeline_is_noop(self):
        applet = _make_applet()
        applet._pipeline = None
        applet._apply_volume()
