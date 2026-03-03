"""Tests for music applet backend selection and UI behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

import docking.applets.music.applet as music_applet_mod
from docking.applets.music import (
    HybridBackend,
    MusicApplet,
    MusicState,
    clamp_percent,
    play_pause_menu_label,
    tooltip_text,
    unavailable_state,
)
from docking.applets.music.render import create_music_icon
from docking.applets.music.state import (
    _normalize_desktop_entry,
    _normalize_volume_percent,
)


def _state(**overrides: object) -> MusicState:
    base = MusicState(
        available=True,
        player_name="Spotify",
        player_bus_name="org.mpris.MediaPlayer2.spotify",
        playback_status="Playing",
        title="Midnight City",
        artist="M83",
        album="Hurry Up, We're Dreaming",
        volume_percent=50,
        can_play_pause=True,
        can_go_next=True,
        can_go_previous=True,
        art_url="",
        track_url="",
    )
    values = {field: getattr(base, field) for field in MusicState.__dataclass_fields__}
    values.update(overrides)
    return MusicState(**values)


class _StubMpris:
    def __init__(self, state: MusicState, action_ok: bool = True) -> None:
        self._state = state
        self._action_ok = action_ok

    def get_state(self) -> MusicState:
        return self._state

    def get_state_for_bus_name(self, bus_name: str) -> MusicState:
        _ = bus_name
        return unavailable_state()

    def has_owner(self, bus_name: str) -> bool:
        _ = bus_name
        return False

    def play_pause(self, _player: str) -> bool:
        return self._action_ok

    def next_track(self, _player: str) -> bool:
        return self._action_ok

    def previous_track(self, _player: str) -> bool:
        return self._action_ok

    def set_volume(self, _player: str, _volume: int) -> bool:
        return self._action_ok


class _StubPlayerctl:
    def __init__(self, state: MusicState, action_ok: bool = True) -> None:
        self._state = state
        self._action_ok = action_ok
        self.play_pause_calls = 0

    def get_state(
        self,
        preferred: str | None = None,
        strict_preferred: bool = False,
    ) -> MusicState:
        if strict_preferred and preferred:
            preferred_norm = preferred.lower()
            state_norm = (
                self._state.player_name or self._state.player_bus_name
            ).lower()
            if preferred_norm not in state_norm and state_norm not in preferred_norm:
                return unavailable_state()
        return self._state

    def play_pause(
        self,
        preferred: str | None = None,
        strict_preferred: bool = False,
    ) -> bool:
        _ = preferred
        _ = strict_preferred
        self.play_pause_calls += 1
        return self._action_ok

    def next_track(
        self,
        preferred: str | None = None,
        strict_preferred: bool = False,
    ) -> bool:
        _ = preferred
        _ = strict_preferred
        return self._action_ok

    def previous_track(
        self,
        preferred: str | None = None,
        strict_preferred: bool = False,
    ) -> bool:
        _ = preferred
        _ = strict_preferred
        return self._action_ok

    def set_volume(
        self,
        preferred: str | None,
        volume_percent: int,
        strict_preferred: bool = False,
    ) -> bool:
        _ = preferred
        _ = volume_percent
        _ = strict_preferred
        return self._action_ok


class _StubRhythmbox:
    def __init__(self, state: MusicState, action_ok: bool = True) -> None:
        self._state = state
        self._action_ok = action_ok
        self.play_pause_calls = 0

    def get_state(self) -> MusicState:
        return self._state

    def play_pause(self) -> bool:
        self.play_pause_calls += 1
        return self._action_ok

    def next_track(self) -> bool:
        return self._action_ok

    def previous_track(self) -> bool:
        return self._action_ok

    def set_volume(self, volume_percent: int) -> bool:
        _ = volume_percent
        return self._action_ok


class TestMusicStateHelpers:
    def test_clamp_percent(self):
        assert clamp_percent(120) == 100
        assert clamp_percent(-1) == 0
        assert clamp_percent(42) == 42

    def test_unavailable_tooltip(self):
        assert tooltip_text(unavailable_state()) == "Music: No active player"

    def test_detailed_tooltip(self):
        text = tooltip_text(_state())
        assert "Spotify" not in text
        assert "Playing" not in text
        assert "M83 - Midnight City" in text
        assert "Vol" not in text
        assert "Album: Hurry Up, We're Dreaming" in text
        assert "\n" in text

    def test_play_pause_menu_label(self):
        assert play_pause_menu_label(_state(playback_status="Playing")) == "Pause"
        assert play_pause_menu_label(_state(playback_status="Paused")) == "Play"

    def test_normalize_volume_percent(self):
        assert _normalize_volume_percent(0.78) == 78
        assert _normalize_volume_percent(78.0) == 78

    def test_normalize_desktop_entry(self):
        assert _normalize_desktop_entry("clementine.desktop") == "clementine"
        assert _normalize_desktop_entry("org.gnome.Rhythmbox3.desktop") == "rhythmbox"
        assert _normalize_desktop_entry("/usr/share/applications/vlc.desktop") == "vlc"

    def test_rhythmbox_unknown_tooltip_avoids_fake_volume(self):
        text = tooltip_text(
            _state(
                player_name="Rhythmbox",
                player_bus_name="org.gnome.Rhythmbox3",
                playback_status="Unknown",
                title="",
                artist="",
                album="",
                volume_percent=100,
            )
        )
        assert text == "Music"
        assert "Rhythmbox" not in text


class TestHybridBackend:
    def test_poll_prefers_mpris(self):
        mpris_state = _state(player_name="MPRIS")
        fallback_state = _state(player_name="playerctl")
        backend = HybridBackend(
            mpris=_StubMpris(state=mpris_state),
            playerctl=_StubPlayerctl(state=fallback_state),
        )
        assert backend.poll().player_name == "MPRIS"

    def test_poll_falls_back_to_playerctl(self):
        backend = HybridBackend(
            mpris=_StubMpris(state=unavailable_state()),
            playerctl=_StubPlayerctl(state=_state(player_name="Fallback")),
            rhythmbox=_StubRhythmbox(state=unavailable_state()),
        )
        assert backend.poll().player_name == "Fallback"

    def test_play_pause_falls_back_when_mpris_action_fails(self):
        playerctl = _StubPlayerctl(state=_state())
        backend = HybridBackend(
            mpris=_StubMpris(state=_state(), action_ok=False),
            playerctl=playerctl,
        )
        assert backend.play_pause(_state()) is True
        assert playerctl.play_pause_calls == 1

    def test_poll_falls_back_to_rhythmbox_when_playerctl_missing(self):
        rb_state = _state(
            player_name="Rhythmbox", player_bus_name="org.gnome.Rhythmbox3"
        )
        backend = HybridBackend(
            mpris=_StubMpris(state=unavailable_state()),
            playerctl=_StubPlayerctl(state=unavailable_state()),
            rhythmbox=_StubRhythmbox(state=rb_state),
        )
        assert backend.poll().player_name == "Rhythmbox"

    def test_play_pause_falls_back_to_rhythmbox(self):
        rhytmbox = _StubRhythmbox(state=_state())
        backend = HybridBackend(
            mpris=_StubMpris(state=_state(), action_ok=False),
            playerctl=_StubPlayerctl(state=_state(), action_ok=False),
            rhythmbox=rhytmbox,
        )
        assert backend.play_pause(_state()) is True
        assert rhytmbox.play_pause_calls == 1

    def test_poll_avoids_unrelated_playerctl_when_rhythmbox_running(self):
        rb_unknown = _state(
            player_name="Rhythmbox",
            player_bus_name="org.gnome.Rhythmbox3",
            playback_status="Unknown",
        )
        backend = HybridBackend(
            mpris=_StubMpris(state=unavailable_state()),
            playerctl=_StubPlayerctl(
                state=_state(
                    player_name="Firefox",
                    player_bus_name="firefox",
                    playback_status="Stopped",
                    volume_percent=100,
                )
            ),
            rhythmbox=_StubRhythmbox(state=rb_unknown),
        )
        selected = backend.poll()
        assert selected.player_name == "Rhythmbox"
        assert selected.player_bus_name == "org.gnome.Rhythmbox3"


def _make_applet(monkeypatch, state: MusicState):
    backend = MagicMock()
    backend.poll.return_value = state
    backend.play_pause.return_value = True
    backend.next_track.return_value = True
    backend.previous_track.return_value = True
    backend.set_volume.return_value = True

    resolver = MagicMock()
    resolver.resolve.return_value = None

    monkeypatch.setattr(music_applet_mod, "HybridBackend", lambda: backend)
    monkeypatch.setattr(music_applet_mod, "CoverArtResolver", lambda: resolver)
    return MusicApplet(48), backend, resolver


class TestMusicApplet:
    def test_creates_with_icon(self, monkeypatch):
        applet, _backend, _resolver = _make_applet(monkeypatch, _state())
        assert applet.item.icon is not None

    def test_on_clicked_toggles_play_pause(self, monkeypatch):
        applet, backend, _resolver = _make_applet(monkeypatch, _state())
        applet.on_clicked()
        backend.play_pause.assert_called_once()

    def test_scroll_up_adjusts_volume(self, monkeypatch):
        applet, backend, _resolver = _make_applet(
            monkeypatch, _state(volume_percent=40)
        )
        applet.on_scroll(direction_up=True)
        backend.set_volume.assert_called_once()
        kwargs = backend.set_volume.call_args.kwargs
        assert kwargs["state"].volume_percent == 40
        assert kwargs["volume_percent"] == 45
        assert applet._state.volume_percent == 45

    def test_scroll_noop_when_unavailable(self, monkeypatch):
        applet, backend, _resolver = _make_applet(monkeypatch, unavailable_state())
        applet.on_scroll(direction_up=True)
        backend.set_volume.assert_not_called()

    def test_menu_when_unavailable(self, monkeypatch):
        applet, _backend, _resolver = _make_applet(monkeypatch, unavailable_state())
        items = applet.get_menu_items()
        assert len(items) == 1
        assert items[0].get_label() == "No active player"
        assert items[0].get_sensitive() is False

    def test_menu_playing_has_pause_label(self, monkeypatch):
        applet, _backend, _resolver = _make_applet(
            monkeypatch, _state(playback_status="Playing")
        )
        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]
        assert "Pause" in labels


class TestMusicRender:
    def test_render_fallback_icon(self):
        pixbuf = create_music_icon(size=48, playback_status="Playing", album_art=None)
        assert pixbuf is not None
        assert pixbuf.get_width() == 48
        assert pixbuf.get_height() == 48
