"""Tests for music applet backend selection and UI behavior."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import docking.applets.music.applet as music_applet_mod
import docking.applets.music.state as music_state_mod
from docking.applets.music.applet import MusicApplet
from docking.applets.music.render import create_music_icon
from docking.applets.music.state import (
    HybridBackend,
    MusicState,
    PlayerctlBackend,
    RhythmboxClientBackend,
    _normalize_desktop_entry,
    _normalize_volume_percent,
    clamp_percent,
    play_pause_menu_label,
    tooltip_text,
    unavailable_state,
)
from docking.core.config import Config
from docking.platform.applications.registry import UnidentifiedApplicationListing
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
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


def _application_listing(
    desktop_id: str = "org.videolan.VLC.desktop",
) -> ApplicationInfo:
    return ApplicationInfo(
        desktop_id=desktop_id,
        name="VLC",
        declared_icon="vlc",
        wm_class="vlc",
        exec_line="vlc %U",
        origin=ApplicationOrigin.INSTALLED,
        location=ApplicationLocation.SANDBOX,
        desktop_file=None,
        executable_path=None,
        aliases=("vlc",),
        visible=True,
        has_gio_source=True,
    )


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
        assert tooltip_text(unavailable_state()) == "Music\nNo active player"

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

    def test_active_media_ignores_idle_and_internal_webkit_players(self):
        idle_browser = _state(
            playback_status="Stopped",
            title="",
            artist="",
            album="",
            track_url="",
            can_play_pause=False,
        )
        internal_webkit = _state(
            player_name="Docking",
            player_bus_name=(
                "org.mpris.MediaPlayer2.org.webkit.app-example.Sandboxed.instance-1"
            ),
            playback_status="Playing",
            title="WhatsApp",
        )

        assert music_state_mod.has_active_media(idle_browser) is False
        assert music_state_mod.has_active_media(internal_webkit) is False
        assert music_state_mod.has_active_media(_state()) is True

    def test_normalize_volume_percent(self):
        assert _normalize_volume_percent(0.78) == 78
        assert _normalize_volume_percent(78.0) == 78

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", ""),
            ("  Clementine.DESKTOP  ", "clementine"),
            ("/usr/share/applications/vlc.desktop", "vlc"),
            (r"C:\usr\share\applications\VLC.desktop", "vlc"),
            ("org.mpris.MediaPlayer2.spotify", "spotify"),
        ],
        ids=["empty", "desktop-id", "unix-path", "windows-path", "mpris-name"],
    )
    def test_normalize_desktop_entry_raw_identifier_forms(self, raw, expected):
        assert _normalize_desktop_entry(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("rhythmbox3", "rhythmbox"),
            ("org.gnome.Rhythmbox3.desktop", "rhythmbox"),
            ("org.videolan.VLC.desktop", "vlc"),
            ("spotify.instance42", "spotify"),
            ("UnregisteredPlayer", "unregisteredplayer"),
        ],
        ids=[
            "rhythmbox3",
            "rhythmbox-reverse-dns",
            "reverse-dns",
            "instance-suffix",
            "unknown",
        ],
    )
    def test_normalize_desktop_entry_preserves_current_fallbacks(self, raw, expected):
        assert _normalize_desktop_entry(raw) == expected

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

    def test_empty_artist_metadata_does_not_leak_list_repr_into_tooltip(self):
        artist = music_state_mod._metadata_artist({})

        text = tooltip_text(_state(title="", artist=artist, album=""))

        assert artist == ""
        assert text == "Music"
        assert "[]" not in text

    def test_normalize_playback_and_misc_helpers(self):
        assert music_state_mod._normalize_playback_status(" paused ") == "Paused"
        assert music_state_mod._normalize_playback_status("unknown") == "Unknown"
        assert music_state_mod._normalize_playback_status("") == "Unknown"
        assert music_state_mod._icon_name_from_bus_name("") == ""
        assert (
            music_state_mod._icon_name_from_bus_name("org.mpris.MediaPlayer2.Spotify")
            == "spotify"
        )

        class _Variant:
            def __init__(self, value):
                self._value = value

            def unpack(self):
                return self._value

        class _BrokenVariant:
            def unpack(self):
                raise RuntimeError("boom")

        broken = _BrokenVariant()
        assert music_state_mod._unpack(_Variant("x")) == "x"
        assert music_state_mod._unpack(broken) is broken
        assert music_state_mod._as_str(None) == ""
        assert music_state_mod._as_str(_Variant("abc")) == "abc"
        assert music_state_mod._as_bool(_Variant(True), default=False) is True
        assert music_state_mod._as_bool("bad", default=True) is True
        assert music_state_mod._as_float("12.5", default=1.0) == 12.5
        assert music_state_mod._as_float("bad", default=1.0) == 1.0
        assert music_state_mod._metadata_str({"a": "b"}, "missing") == ""
        assert music_state_mod._metadata_artist({"xesam:artist": ["A", "B"]}) == "A"
        assert music_state_mod._metadata_artist({"xesam:artist": "Solo"}) == "Solo"

    def test_tooltip_with_title_only(self):
        text = tooltip_text(_state(artist="", album="", title="Only Title"))
        assert text == "Music\nOnly Title"


class TestMediaAppDiscovery:
    def test_prefers_default_audio_application(self, monkeypatch):
        app_info = MagicMock()
        get_default = MagicMock(return_value=app_info)
        monkeypatch.setattr(
            music_applet_mod.Gio.AppInfo,
            "get_default_for_type",
            get_default,
        )

        assert music_applet_mod._find_media_app() is app_info
        get_default.assert_called_once_with("audio/mpeg", False)

    def test_falls_back_to_recommended_media_application(self, monkeypatch):
        app_info = MagicMock()
        app_info.should_show.return_value = True
        monkeypatch.setattr(
            music_applet_mod.Gio.AppInfo,
            "get_default_for_type",
            lambda _content_type, _must_support_uris: None,
        )
        get_recommended = MagicMock(return_value=[app_info])
        monkeypatch.setattr(
            music_applet_mod.Gio.AppInfo,
            "get_recommended_for_type",
            get_recommended,
        )

        assert music_applet_mod._find_media_app() is app_info
        get_recommended.assert_called_once_with("audio/mpeg")

    def test_launches_media_application_directly(self, monkeypatch):
        app_info = MagicMock()
        app_info.get_id.return_value = "org.videolan.VLC.desktop"
        app_info.launch.return_value = True
        monkeypatch.setattr(
            music_applet_mod,
            "_find_media_app",
            lambda: app_info,
        )
        assert music_applet_mod.launch_default_media_app() is True
        app_info.launch.assert_called_once_with([], None)


class TestMprisBackendInternals:
    def _make_backend(self):
        backend = object.__new__(music_state_mod.MprisBackend)
        backend._last_active_bus_name = ""
        backend._bus = object()
        backend._dbus_proxy = None
        backend._player_proxies = {}
        backend._props_proxies = {}
        return backend

    def test_get_state_and_get_state_for_bus_name(self):
        backend = self._make_backend()
        s1 = _state(player_bus_name="org.mpris.MediaPlayer2.spotify")
        s2 = _state(
            player_bus_name="org.mpris.MediaPlayer2.vlc", playback_status="Paused"
        )
        backend.list_players = lambda: [s1.player_bus_name, s2.player_bus_name]  # type: ignore[method-assign]
        backend._read_state = lambda bus_name: s1 if "spotify" in bus_name else s2  # type: ignore[method-assign]
        backend._select_player = lambda states: states[-1]  # type: ignore[method-assign]
        selected = backend.get_state()
        assert selected.player_bus_name == s2.player_bus_name
        assert backend._last_active_bus_name == s2.player_bus_name

        backend.has_owner = lambda bus_name: False  # type: ignore[method-assign]
        assert (
            backend.get_state_for_bus_name("org.mpris.MediaPlayer2.spotify").available
            is False
        )
        backend.has_owner = lambda bus_name: True  # type: ignore[method-assign]
        backend._read_state = lambda bus_name: None  # type: ignore[method-assign]
        assert (
            backend.get_state_for_bus_name("org.mpris.MediaPlayer2.spotify").available
            is False
        )
        backend._read_state = lambda bus_name: s1  # type: ignore[method-assign]
        state = backend.get_state_for_bus_name("org.mpris.MediaPlayer2.spotify")
        assert state.available is True
        assert backend._last_active_bus_name == s1.player_bus_name

    def test_get_state_unavailable_when_no_players(self):
        backend = self._make_backend()
        backend.list_players = list  # type: ignore[method-assign]
        assert backend.get_state().available is False

    def test_get_state_ignores_internal_webkit_player(self):
        backend = self._make_backend()
        internal_webkit = _state(
            player_name="Docking",
            player_bus_name=(
                "org.mpris.MediaPlayer2.org.webkit.app-example.Sandboxed.instance-1"
            ),
            playback_status="Playing",
            title="WhatsApp",
        )
        backend.list_players = lambda: [internal_webkit.player_bus_name]  # type: ignore[method-assign]
        backend._read_state = lambda bus_name: internal_webkit  # type: ignore[method-assign]

        assert backend.get_state().available is False

    def test_has_owner_and_list_players_branches(self, monkeypatch):
        backend = self._make_backend()
        backend._dbus_proxy = None
        assert backend.has_owner("x") is False
        assert backend.list_players() == []

        class _Result:
            def __init__(self, payload):
                self._payload = payload

            def unpack(self):
                return self._payload

        class _Proxy:
            def call_sync(self, method, *_args, **_kwargs):
                if method == "NameHasOwner":
                    return _Result((True,))
                return _Result(
                    (
                        [
                            "org.mpris.MediaPlayer2.spotify",
                            "org.other.Service",
                            "org.mpris.MediaPlayer2.vlc",
                        ],
                    )
                )

        backend._dbus_proxy = _Proxy()
        assert backend.has_owner("org.mpris.MediaPlayer2.spotify") is True
        assert backend.list_players() == [
            "org.mpris.MediaPlayer2.spotify",
            "org.mpris.MediaPlayer2.vlc",
        ]

        class _ProxyFail:
            def call_sync(self, *_args, **_kwargs):
                raise Exception("boom")

        backend._dbus_proxy = _ProxyFail()
        monkeypatch.setattr(music_state_mod.GLib, "Error", Exception)
        assert backend.has_owner("x") is False
        assert backend.list_players() == []

    def test_set_volume_and_select_player(self, monkeypatch):
        backend = self._make_backend()
        backend._get_props_proxy = lambda bus_name: None  # type: ignore[method-assign]
        assert backend.set_volume("bus", 50) is False

        class _Props:
            def __init__(self, fail=False):
                self.fail = fail

            def call_sync(self, *_args, **_kwargs):
                if self.fail:
                    raise Exception("fail")
                return object()

        monkeypatch.setattr(music_state_mod.GLib, "Error", Exception)
        backend._get_props_proxy = lambda bus_name: _Props(fail=False)  # type: ignore[method-assign]
        assert backend.set_volume("bus", 50) is True
        backend._get_props_proxy = lambda bus_name: _Props(fail=True)  # type: ignore[method-assign]
        assert backend.set_volume("bus", 50) is False

        playing_a = _state(player_bus_name="a", playback_status="Playing")
        playing_b = _state(player_bus_name="b", playback_status="Playing")
        backend._last_active_bus_name = "b"
        assert backend._select_player([playing_a, playing_b]).player_bus_name == "b"
        backend._last_active_bus_name = "x"
        assert backend._select_player([playing_a, playing_b]).player_bus_name == "a"

    def test_player_display_name_and_read_state(self):
        backend = self._make_backend()
        values = {
            (
                "org.mpris.MediaPlayer2.spotify",
                music_state_mod._MPRIS_ROOT_IFACE,
                "Identity",
            ): "Spotify",
            (
                "org.mpris.MediaPlayer2.spotify",
                music_state_mod._MPRIS_ROOT_IFACE,
                "DesktopEntry",
            ): "spotify.desktop",
            (
                "org.mpris.MediaPlayer2.spotify",
                music_state_mod._MPRIS_PLAYER_IFACE,
                "Metadata",
            ): {
                "xesam:title": "Song",
                "xesam:artist": ["Artist"],
                "xesam:album": "Album",
                "mpris:artUrl": "http://art",
                "xesam:url": "file:///song",
            },
            (
                "org.mpris.MediaPlayer2.spotify",
                music_state_mod._MPRIS_PLAYER_IFACE,
                "PlaybackStatus",
            ): "Playing",
            (
                "org.mpris.MediaPlayer2.spotify",
                music_state_mod._MPRIS_PLAYER_IFACE,
                "Volume",
            ): 0.42,
            (
                "org.mpris.MediaPlayer2.spotify",
                music_state_mod._MPRIS_PLAYER_IFACE,
                "CanPlay",
            ): True,
            (
                "org.mpris.MediaPlayer2.spotify",
                music_state_mod._MPRIS_PLAYER_IFACE,
                "CanPause",
            ): True,
            (
                "org.mpris.MediaPlayer2.spotify",
                music_state_mod._MPRIS_PLAYER_IFACE,
                "CanGoNext",
            ): True,
            (
                "org.mpris.MediaPlayer2.spotify",
                music_state_mod._MPRIS_PLAYER_IFACE,
                "CanGoPrevious",
            ): False,
        }
        backend._get_props_proxy = lambda bus_name: object()  # type: ignore[method-assign]
        backend._get_property = lambda *, bus_name, interface_name, property_name: (
            values.get(  # type: ignore[method-assign]
                (bus_name, interface_name, property_name)
            )
        )
        state = backend._read_state("org.mpris.MediaPlayer2.spotify")
        assert state is not None
        assert state.player_name == "Spotify"
        assert state.player_icon_name == "spotify"
        assert state.player_desktop_entry == "spotify.desktop"
        assert state.title == "Song"
        assert state.artist == "Artist"
        assert state.volume_percent == 42
        assert state.can_go_previous is False

        backend._get_props_proxy = lambda bus_name: None  # type: ignore[method-assign]
        assert backend._read_state("org.mpris.MediaPlayer2.spotify") is None
        backend._get_property = lambda **kwargs: None  # type: ignore[method-assign]
        assert (
            backend._player_display_name("org.mpris.MediaPlayer2.firefox.instance")
            == "Firefox"
        )

    @pytest.mark.parametrize(
        ("bus_name", "expected_icon"),
        [
            ("org.mpris.MediaPlayer2.rhythmbox3", "rhythmbox"),
            ("org.mpris.MediaPlayer2.org.videolan.VLC", "vlc"),
            ("org.mpris.MediaPlayer2.spotify.instance42", "spotify"),
            ("org.mpris.MediaPlayer2.UnregisteredPlayer", "unregisteredplayer"),
        ],
        ids=["rhythmbox3", "reverse-dns", "instance-suffix", "unknown"],
    )
    def test_read_state_without_desktop_entry_uses_bus_name_fallback(
        self, bus_name, expected_icon
    ):
        backend = self._make_backend()
        backend._get_props_proxy = lambda bus_name: object()  # type: ignore[method-assign]
        backend._get_property = lambda **kwargs: None  # type: ignore[method-assign]

        state = backend._read_state(bus_name)

        assert state is not None
        assert state.player_icon_name == expected_icon
        assert state.player_desktop_entry == ""

    def test_get_property_call_method_and_proxy_caches(self, monkeypatch):
        backend = self._make_backend()
        monkeypatch.setattr(music_state_mod.GLib, "Error", Exception)

        class _Result:
            def __init__(self, payload):
                self._payload = payload

            def unpack(self):
                return self._payload

        class _Props:
            def __init__(self, fail=False):
                self.fail = fail

            def call_sync(self, *_args, **_kwargs):
                if self.fail:
                    raise Exception("bad")
                return _Result(("value",))

        backend._get_props_proxy = lambda bus_name: None  # type: ignore[method-assign]
        assert (
            backend._get_property(
                bus_name="x",
                interface_name="i",
                property_name="p",
            )
            is None
        )
        backend._get_props_proxy = lambda bus_name: _Props()  # type: ignore[method-assign]
        assert (
            backend._get_property(
                bus_name="x",
                interface_name="i",
                property_name="p",
            )
            == "value"
        )
        backend._get_props_proxy = lambda bus_name: _Props(fail=True)  # type: ignore[method-assign]
        assert (
            backend._get_property(
                bus_name="x",
                interface_name="i",
                property_name="p",
            )
            is None
        )

        class _Player:
            def __init__(self, fail=False):
                self.fail = fail

            def call_sync(self, *_args, **_kwargs):
                if self.fail:
                    raise Exception("bad")
                return object()

        backend._get_player_proxy = lambda bus_name: None  # type: ignore[method-assign]
        assert backend._call_player_method("x", "PlayPause") is False
        backend._get_player_proxy = lambda bus_name: _Player()  # type: ignore[method-assign]
        assert backend._call_player_method("x", "PlayPause") is True
        backend._get_player_proxy = lambda bus_name: _Player(fail=True)  # type: ignore[method-assign]
        assert backend._call_player_method("x", "PlayPause") is False

        backend._get_player_proxy = (
            music_state_mod.MprisBackend._get_player_proxy.__get__(  # type: ignore[method-assign]
                backend,
                music_state_mod.MprisBackend,
            )
        )
        backend._get_props_proxy = (
            music_state_mod.MprisBackend._get_props_proxy.__get__(  # type: ignore[method-assign]
                backend,
                music_state_mod.MprisBackend,
            )
        )
        backend._bus = None
        assert backend._get_player_proxy("x") is None
        assert backend._get_props_proxy("x") is None

        backend._bus = object()

        class _ProxyFactory:
            def __init__(self):
                self.created = []

            def __call__(self, *args, **kwargs):
                proxy = object()
                self.created.append(proxy)
                return proxy

        factory = _ProxyFactory()
        monkeypatch.setattr(music_state_mod.Gio.DBusProxy, "new_sync", factory)
        p1 = backend._get_player_proxy("bus.one")
        p2 = backend._get_player_proxy("bus.one")
        props1 = backend._get_props_proxy("bus.one")
        props2 = backend._get_props_proxy("bus.one")
        assert p1 is p2
        assert props1 is props2

        monkeypatch.setattr(
            music_state_mod.Gio.DBusProxy,
            "new_sync",
            lambda *args, **kwargs: (_ for _ in ()).throw(Exception("bad")),
        )
        assert backend._get_player_proxy("bus.fail") is None
        assert backend._get_props_proxy("bus.fail") is None


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


def _make_applet(
    monkeypatch,
    state: MusicState,
    *,
    registry=None,
    launcher=None,
):
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
    if registry is None:
        registry = MagicMock()
        registry.resolve.return_value = None
        registry.default_listing_for_content_type.return_value = None
        registry.recommended_listings_for_content_type.return_value = ()
    return (
        MusicApplet(
            48,
            config=Config(),
            application_registry=registry,
            application_launcher=launcher or MagicMock(),
        ),
        backend,
        resolver,
    )


class TestMusicApplet:
    def test_creates_with_icon(self, monkeypatch):
        applet, _backend, _resolver = _make_applet(monkeypatch, _state())
        assert applet.item.icon is not None

    def test_constructor_performs_initial_poll_and_artwork_resolution(
        self, monkeypatch
    ):
        initial = _state(title="Initial")

        applet, backend, resolver = _make_applet(monkeypatch, initial)

        backend.poll.assert_called_once_with()
        resolver.resolve.assert_called_once_with(state=initial)
        assert applet._state == initial
        assert applet._album_art is None

    def test_constructor_resolves_initial_state_only_once(self, monkeypatch):
        initial = _state(
            player_icon_name="vlc",
            player_desktop_entry="org.videolan.VLC",
        )
        application = SimpleNamespace(declared_icon="vlc-installed")
        registry = MagicMock()
        registry.resolve.return_value = application
        launcher = MagicMock()
        applet, _backend, _resolver = _make_applet(
            monkeypatch,
            initial,
            registry=registry,
            launcher=launcher,
        )

        registry.resolve.assert_called_once_with(
            "org.videolan.VLC.desktop",
            log_failures=False,
        )
        registry.refresh.assert_not_called()
        assert applet._state.player_icon_name == "vlc-installed"
        assert applet._state.player_desktop_entry == "org.videolan.VLC"

    @pytest.mark.parametrize(
        ("raw", "installed_id"),
        [
            ("spotify", "spotify.desktop"),
            ("org.videolan.VLC", "org.videolan.VLC.desktop"),
            (
                "org.mpris.MediaPlayer2.org.videolan.VLC",
                "org.videolan.VLC.desktop",
            ),
            ("rhythmbox3", "org.gnome.Rhythmbox3.desktop"),
        ],
        ids=["suffix", "reverse-dns", "mpris-prefix", "rhythmbox3"],
    )
    def test_registry_icon_resolution_preserves_desktop_entry_heuristics(
        self,
        monkeypatch,
        raw,
        installed_id,
    ):
        initial = _state(
            player_icon_name="worker-fallback",
            player_desktop_entry=raw,
        )
        application = SimpleNamespace(declared_icon=f"installed:{installed_id}")
        registry = MagicMock()
        registry.resolve.side_effect = lambda desktop_id, **_kwargs: (
            application if desktop_id == installed_id else None
        )

        applet, _backend, _resolver = _make_applet(
            monkeypatch,
            initial,
            registry=registry,
        )

        assert applet._state.player_icon_name == f"installed:{installed_id}"
        registry.refresh.assert_not_called()

    def test_unresolved_registry_icon_keeps_worker_fallback_byte_for_byte(
        self, monkeypatch
    ):
        fallback = "MiXeD/Icon Name.desktop"
        initial = _state(
            player_icon_name=fallback,
            player_desktop_entry="org.example.MissingPlayer",
        )
        registry = MagicMock()
        registry.resolve.return_value = None
        applet, _backend, _resolver = _make_applet(
            monkeypatch,
            initial,
            registry=registry,
        )

        assert applet._state.player_icon_name == fallback
        assert applet._state is initial
        registry.refresh.assert_not_called()

    def test_on_clicked_toggles_play_pause(self, monkeypatch):
        applet, backend, _resolver = _make_applet(monkeypatch, _state())
        applet.on_clicked()
        backend.play_pause.assert_called_once()

    def test_on_clicked_launches_media_app_when_no_player_is_open(self, monkeypatch):
        application = _application_listing()
        registry = MagicMock()
        registry.default_listing_for_content_type.return_value = application
        launcher = MagicMock()
        launcher.launch.return_value = True
        applet, backend, _resolver = _make_applet(
            monkeypatch,
            unavailable_state(),
            registry=registry,
            launcher=launcher,
        )

        applet.on_clicked()

        launcher.launch.assert_called_once_with("org.videolan.VLC.desktop")
        backend.play_pause.assert_not_called()

    def test_on_clicked_ignores_idle_empty_browser_mpris_service(self, monkeypatch):
        idle_browser = _state(
            player_name="Chrome",
            player_bus_name="org.mpris.MediaPlayer2.chromium.instance1",
            playback_status="Stopped",
            title="",
            artist="",
            album="",
            track_url="",
            can_play_pause=False,
        )
        application = _application_listing()
        registry = MagicMock()
        registry.default_listing_for_content_type.return_value = application
        launcher = MagicMock()
        launcher.launch.return_value = True
        applet, backend, _resolver = _make_applet(
            monkeypatch,
            idle_browser,
            registry=registry,
            launcher=launcher,
        )

        applet.on_clicked()

        launcher.launch.assert_called_once_with("org.videolan.VLC.desktop")
        backend.play_pause.assert_not_called()

    def test_on_clicked_ignores_internal_webkit_media_service(self, monkeypatch):
        internal_webkit = _state(
            player_name="Docking",
            player_bus_name=(
                "org.mpris.MediaPlayer2.org.webkit.app-example.Sandboxed.instance-1"
            ),
            playback_status="Playing",
            title="WhatsApp",
        )
        application = _application_listing()
        registry = MagicMock()
        registry.default_listing_for_content_type.return_value = application
        launcher = MagicMock()
        launcher.launch.return_value = True
        applet, backend, _resolver = _make_applet(
            monkeypatch,
            internal_webkit,
            registry=registry,
            launcher=launcher,
        )

        applet.on_clicked()

        launcher.launch.assert_called_once_with("org.videolan.VLC.desktop")
        backend.play_pause.assert_not_called()

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

    def test_start_stop_and_tick(self, monkeypatch):
        applet, _backend, _resolver = _make_applet(monkeypatch, _state())
        removed: list[int] = []
        monkeypatch.setattr(
            music_applet_mod.GLib,
            "timeout_add_seconds",
            lambda sec, cb: 11,
        )
        monkeypatch.setattr(
            music_applet_mod.GLib,
            "source_remove",
            lambda source_id: removed.append(source_id),
        )
        applet.start(notify=lambda: None)
        assert applet._timer_id == 11

        applet._scroll_sync_id = 22
        applet.stop()
        assert applet._timer_id == 0
        assert applet._scroll_sync_id == 0
        assert removed == [22, 11]

        calls: list[str] = []

        def fake_run_guarded(*, key, name, fn, on_result=None, on_error=None):
            _ = name, on_error
            calls.append(key)
            result = fn()
            if on_result is not None:
                on_result(result)
            return True

        applet._worker.run_guarded = fake_run_guarded  # type: ignore[method-assign]
        applet._poll_worker = lambda: (calls.append("poll"), (_state(), None))[1]  # type: ignore[assignment]
        assert applet._tick() is True
        assert calls == ["poll", "poll"]

    def test_tick_applies_poll_only_through_async_result_callback(self, monkeypatch):
        initial = _state(title="Initial")
        updated = _state(title="Async update")
        applet, backend, resolver = _make_applet(monkeypatch, initial)
        artwork = object()
        backend.poll.return_value = updated
        resolver.resolve.return_value = artwork
        applet.present = MagicMock()
        applet._worker.run_guarded = MagicMock(return_value=True)  # type: ignore[method-assign]

        assert applet._tick() is True

        scheduled = applet._worker.run_guarded.call_args.kwargs
        assert scheduled["key"] == "poll"
        assert scheduled["name"] == "music-poll"
        result = scheduled["fn"]()
        assert result == (updated, artwork)
        assert applet._state == initial

        assert scheduled["on_result"](result) is False
        assert applet._state == updated
        assert applet._album_art is artwork
        applet.present.assert_called_once_with()

    def test_poll_worker_defers_registry_and_theme_access_to_result_callback(
        self, monkeypatch
    ):
        initial = _state(
            player_icon_name="initial",
            player_desktop_entry="initial",
        )
        updated = _state(
            title="Worker result",
            player_icon_name="worker-fallback",
            player_desktop_entry="org.example.Player",
        )
        registry = MagicMock()
        registry.resolve.return_value = SimpleNamespace(declared_icon="installed")
        applet, backend, resolver = _make_applet(
            monkeypatch,
            initial,
            registry=registry,
        )
        registry.reset_mock()
        backend.poll.return_value = updated
        theme_lookup = MagicMock(side_effect=AssertionError("theme lookup in worker"))
        monkeypatch.setattr(
            music_applet_mod.Gtk.IconTheme,
            "get_default",
            theme_lookup,
        )

        result = applet._poll_worker()

        assert result == (updated, resolver.resolve.return_value)
        registry.resolve.assert_not_called()
        registry.refresh.assert_not_called()
        theme_lookup.assert_not_called()

        applet._on_poll_result(result)
        registry.resolve.assert_called_once_with(
            "org.example.Player.desktop",
            log_failures=False,
        )
        assert applet._state.player_icon_name == "installed"

    def test_sync_refresh_resolves_registry_icon_on_calling_thread(self, monkeypatch):
        initial = _state(title="Initial")
        updated = _state(
            title="Synchronous update",
            player_icon_name="worker-fallback",
            player_desktop_entry="org.example.Player",
        )
        registry = MagicMock()
        registry.resolve.return_value = SimpleNamespace(declared_icon="installed")
        applet, backend, resolver = _make_applet(
            monkeypatch,
            initial,
            registry=registry,
        )
        registry.reset_mock()
        backend.poll.return_value = updated
        resolver.resolve.return_value = object()
        applet.present = MagicMock()

        applet._refresh_now()

        registry.resolve.assert_called_once_with(
            "org.example.Player.desktop",
            log_failures=False,
        )
        registry.refresh.assert_not_called()
        assert applet._state.player_icon_name == "installed"

    def test_refresh_now_polls_and_applies_result_synchronously(self, monkeypatch):
        initial = _state(title="Initial")
        updated = _state(title="Synchronous update")
        applet, backend, resolver = _make_applet(monkeypatch, initial)
        artwork = object()
        backend.poll.reset_mock()
        resolver.resolve.reset_mock()
        backend.poll.return_value = updated
        resolver.resolve.return_value = artwork
        applet.present = MagicMock()
        applet._worker.run_guarded = MagicMock()  # type: ignore[method-assign]

        applet._refresh_now()

        backend.poll.assert_called_once_with()
        resolver.resolve.assert_called_once_with(state=updated)
        applet._worker.run_guarded.assert_not_called()
        assert applet._state == updated
        assert applet._album_art is artwork
        applet.present.assert_called_once_with()

    def test_action_methods_poll_and_volume_alias(self, monkeypatch):
        applet, backend, _resolver = _make_applet(monkeypatch, _state())
        applet._refresh_now = MagicMock()

        backend.previous_track.return_value = True
        applet._action_previous()
        backend.previous_track.return_value = False
        applet._action_previous()

        backend.play_pause.return_value = True
        applet._action_play_pause()
        backend.play_pause.return_value = False
        applet._action_play_pause()

        backend.next_track.return_value = True
        applet._action_next()
        backend.next_track.return_value = False
        applet._action_next()

        applet.on_scroll = MagicMock()
        applet._action_volume(direction_up=True)
        applet.on_scroll.assert_called_once_with(direction_up=True)

        assert applet._refresh_now.call_count == 3

    def test_poll_worker_and_apply_poll_result(self, monkeypatch):
        applet, backend, resolver = _make_applet(monkeypatch, _state())
        polled = _state(title="New")
        backend.poll.return_value = polled
        art = MagicMock()
        resolver.resolve.return_value = art
        state, resolved_art = applet._poll_worker()
        assert state == polled
        assert resolved_art is art

        applet.present = MagicMock()
        applet._state = polled
        applet._album_art = art
        assert applet._apply_poll_result(polled, art) is False
        applet.present.assert_not_called()
        assert applet._apply_poll_result(_state(title="Other"), None) is False
        applet.present.assert_called_once()

    def test_scroll_sync_and_tooltip_widget(self, monkeypatch):
        applet, _backend, _resolver = _make_applet(monkeypatch, _state())
        removed: list[int] = []
        monkeypatch.setattr(
            music_applet_mod.GLib,
            "source_remove",
            lambda source_id: removed.append(source_id),
        )
        monkeypatch.setattr(
            music_applet_mod.GLib,
            "timeout_add",
            lambda delay_ms, cb: 44,
        )
        applet._scroll_sync_id = 33
        applet._schedule_scroll_sync()
        assert removed == [33]
        assert applet._scroll_sync_id == 44

        calls: list[str] = []

        def fake_run_guarded(*, key, name, fn, on_result=None, on_error=None):
            _ = name, fn, on_result, on_error
            calls.append(key)
            return True

        applet._worker.run_guarded = fake_run_guarded  # type: ignore[method-assign]
        assert applet._run_scroll_sync() is False
        assert applet._scroll_sync_id == 0
        assert calls == ["poll"]

        applet._state = _state(title="Song", artist="Artist", album="Album")

        class _FakeBox:
            def __init__(self, orientation=None, spacing=0):
                self._children: list[object] = []

            def pack_start(self, child, expand, fill, padding):
                _ = expand, fill, padding
                self._children.append(child)

            def get_children(self):
                return self._children

        class _FakeLabel:
            def __init__(self, label=""):
                self._label = label

            def set_xalign(self, value):
                _ = value

            def set_justify(self, value):
                _ = value

            def override_color(self, state, rgba):
                _ = state, rgba

        monkeypatch.setattr(
            music_applet_mod,
            "Gtk",
            SimpleNamespace(
                Box=_FakeBox,
                Label=_FakeLabel,
                Orientation=SimpleNamespace(VERTICAL=1),
                Justification=SimpleNamespace(CENTER=1),
                StateFlags=SimpleNamespace(NORMAL=1),
            ),
        )

        widget = applet._build_tooltip_widget()
        assert widget.get_children()

    def test_registry_media_lookup_preserves_type_order_and_visibility(
        self, monkeypatch
    ):
        visible = _application_listing("visible.desktop")
        registry = MagicMock()
        registry.default_listing_for_content_type.return_value = None
        registry.recommended_listings_for_content_type.side_effect = (
            lambda content_type: {
                "audio/mpeg": (),
                "audio/flac": (visible,),
            }.get(content_type, ())
        )
        applet, _backend, _resolver = _make_applet(
            monkeypatch,
            unavailable_state(),
            registry=registry,
        )

        selected = applet._find_media_application()

        assert selected is visible
        assert [
            call.args[0]
            for call in registry.default_listing_for_content_type.call_args_list
        ] == list(music_applet_mod.MEDIA_CONTENT_TYPES)
        assert [
            call.args[0]
            for call in registry.recommended_listings_for_content_type.call_args_list
        ] == ["audio/mpeg", "audio/flac"]
        registry.refresh.assert_not_called()

    def test_click_launches_registry_selected_media_app_through_launcher(
        self, monkeypatch
    ):
        application = _application_listing()
        registry = MagicMock()
        registry.default_listing_for_content_type.return_value = application
        launcher = MagicMock()
        launcher.launch.return_value = True
        applet, backend, _resolver = _make_applet(
            monkeypatch,
            unavailable_state(),
            registry=registry,
            launcher=launcher,
        )
        legacy_launch = MagicMock(side_effect=AssertionError("legacy launch used"))
        monkeypatch.setattr(
            music_applet_mod,
            "launch_default_media_app",
            legacy_launch,
        )
        applet.on_clicked()

        launcher.launch.assert_called_once_with("org.videolan.VLC.desktop")
        legacy_launch.assert_not_called()
        backend.play_pause.assert_not_called()
        registry.refresh.assert_not_called()

    def test_click_launches_transient_media_handler_by_opaque_listing_key(
        self, monkeypatch
    ):
        listing = UnidentifiedApplicationListing(
            listing_key="gio-content:4:2",
            name="Unregistered Media Handler",
            categories="AudioVideo;",
            icon_name="media-handler",
            desktop_file=None,
            exec_line="media-handler %U",
            description="Play media",
            generic_name="Media Player",
        )
        registry = MagicMock()
        registry.default_listing_for_content_type.return_value = listing
        launcher = MagicMock()
        launcher.launch_listing.return_value = True
        applet, backend, _resolver = _make_applet(
            monkeypatch,
            unavailable_state(),
            registry=registry,
            launcher=launcher,
        )

        applet.on_clicked()

        launcher.launch_listing.assert_called_once_with("gio-content:4:2")
        launcher.launch.assert_not_called()
        backend.play_pause.assert_not_called()


class TestMusicRender:
    def test_render_fallback_icon(self):
        pixbuf = create_music_icon(size=48, playback_status="Playing", album_art=None)
        assert pixbuf is not None
        assert pixbuf.get_width() == 48
        assert pixbuf.get_height() == 48


class TestPlayerctlBackendInternals:
    def _make_backend(self) -> PlayerctlBackend:
        backend = object.__new__(PlayerctlBackend)
        backend._last_active_player = ""
        backend._binary = "playerctl"
        return backend

    def test_list_players_deduplicates_and_preserves_order(self):
        backend = self._make_backend()
        backend._run = lambda **_kwargs: "vlc\nspotify\nvlc\n\n"
        assert backend._list_players() == ["vlc", "spotify"]

    def test_match_player_name_handles_mpris_bus_name(self):
        backend = self._make_backend()
        assert (
            backend._match_player_name(
                players=["rhythmbox", "spotify"],
                preferred="org.mpris.MediaPlayer2.spotify.instance100",
            )
            == "spotify"
        )

    def test_select_player_strict_preferred_returns_none(self):
        backend = self._make_backend()
        backend._list_players = lambda: ["spotify", "vlc"]  # type: ignore[method-assign]
        assert (
            backend._select_player(preferred="rhythmbox", strict_preferred=True) is None
        )

    def test_select_player_prefers_playing_when_no_last_active(self):
        backend = self._make_backend()
        backend._list_players = lambda: ["spotify", "vlc"]  # type: ignore[method-assign]

        def fake_run(*, cmd, timeout):
            _ = timeout
            return "Playing\n" if cmd[-2:] == ["spotify", "status"] else "Paused\n"

        backend._run = fake_run  # type: ignore[method-assign]
        assert backend._select_player() == "spotify"

    def test_set_volume_retries_with_relaxed_match_when_strict_fails(self):
        backend = self._make_backend()
        calls: list[tuple[str | None, bool]] = []

        def fake_select_player(*, preferred, strict_preferred=False):
            calls.append((preferred, strict_preferred))
            if strict_preferred:
                return None
            return "spotify"

        backend._select_player = fake_select_player  # type: ignore[method-assign]
        backend._run = lambda **_kwargs: ""

        assert (
            backend.set_volume(
                preferred="org.mpris.MediaPlayer2.spotify.instance100",
                volume_percent=42,
            )
            is True
        )
        assert calls == [
            ("org.mpris.MediaPlayer2.spotify.instance100", True),
            ("org.mpris.MediaPlayer2.spotify.instance100", False),
        ]

    def test_run_handles_timeout_and_returns_none(self, monkeypatch):
        backend = self._make_backend()

        def fail_run(*_a, **_k):
            raise subprocess.TimeoutExpired(cmd="playerctl", timeout=1.0)

        monkeypatch.setattr(music_state_mod.subprocess, "run", fail_run)
        assert backend._run(cmd=["playerctl", "-l"], timeout=1.0) is None


class TestRhythmboxClientBackendInternals:
    def _make_backend(self) -> RhythmboxClientBackend:
        backend = object.__new__(RhythmboxClientBackend)
        backend._binary = "rhythmbox-client"
        backend._gdbus_binary = "gdbus"
        backend._settings = None
        return backend

    def test_read_volume_percent_falls_back_to_print_volume(self):
        backend = self._make_backend()
        backend._run = lambda **_kwargs: "Playback volume is 0.73"
        assert backend._read_volume_percent() == 73

    def test_read_volume_percent_returns_zero_on_invalid_output(self):
        backend = self._make_backend()
        backend._run = lambda **_kwargs: "no volume token here"
        assert backend._read_volume_percent() == 0

    def test_run_action_uses_gtk_fallback_when_client_fails(self):
        backend = self._make_backend()
        backend._run = lambda **_kwargs: None
        backend._activate_gtk_action = lambda **_kwargs: True
        assert backend._run_action("--play-pause", gtk_action="play") is True

    def test_activate_gtk_action_handles_subprocess_error(self, monkeypatch):
        backend = self._make_backend()

        def fail_run(*_a, **_k):
            raise OSError("gdbus missing")

        monkeypatch.setattr(music_state_mod.subprocess, "run", fail_run)
        assert backend._activate_gtk_action(action_name="play") is False

    def test_get_state_and_set_volume_and_is_running(self, monkeypatch):
        backend = self._make_backend()
        backend._binary = None
        assert backend.get_state().available is False
        assert backend.set_volume(50) is False
        assert backend._is_running() is False

        backend = self._make_backend()
        backend._is_running = lambda: True  # type: ignore[method-assign]
        backend._run = lambda **_kwargs: None
        assert backend.get_state().available is False

        backend._run = lambda **kwargs: "Song\tArtist\tAlbum\tfile:///song"
        backend._read_volume_percent = lambda: 55  # type: ignore[method-assign]
        state = backend.get_state()
        assert state.available is True
        assert state.playback_status == "Playing"
        assert state.title == "Song"
        assert state.volume_percent == 55
        assert state.player_desktop_entry == "org.gnome.Rhythmbox3"

        backend._run = lambda **kwargs: ""
        assert backend.set_volume(50) is True

    def test_read_volume_percent_prefers_settings_and_handles_errors(self):
        backend = self._make_backend()

        class _Settings:
            def get_double(self, key):
                return 0.64

        backend._settings = _Settings()
        assert backend._read_volume_percent() == 64

        class _BrokenSettings:
            def get_double(self, key):
                raise RuntimeError("boom")

        backend._settings = _BrokenSettings()
        backend._run = lambda **_kwargs: "Playback volume is 0.20"
        assert backend._read_volume_percent() == 20

    def test_is_running_and_run_helpers(self, monkeypatch):
        backend = self._make_backend()

        class _Proc:
            def __init__(self, returncode=0, stdout=""):
                self.returncode = returncode
                self.stdout = stdout

        monkeypatch.setattr(
            music_state_mod.subprocess,
            "run",
            lambda *args, **kwargs: _Proc(returncode=0, stdout="ok"),
        )
        assert backend._is_running() is True
        assert backend._run(["rhythmbox-client"], timeout=1.0) == "ok"

        monkeypatch.setattr(
            music_state_mod.subprocess,
            "run",
            lambda *args, **kwargs: _Proc(returncode=1, stdout="bad"),
        )
        assert backend._is_running() is False
        assert backend._run(["rhythmbox-client"], timeout=1.0) is None

        def raise_os(*_a, **_k):
            raise OSError("missing")

        monkeypatch.setattr(music_state_mod.subprocess, "run", raise_os)
        assert backend._is_running() is False
        assert backend._run(["rhythmbox-client"], timeout=1.0) is None


class TestHybridBackendActions:
    def test_next_track_prefers_rhythmbox_state_backend(self):
        rb = _StubRhythmbox(state=_state(), action_ok=True)
        backend = HybridBackend(
            mpris=_StubMpris(state=_state(), action_ok=False),
            playerctl=_StubPlayerctl(state=_state(), action_ok=False),
            rhythmbox=rb,
        )
        assert (
            backend.next_track(_state(player_bus_name=music_state_mod._RB_SERVICE))
            is True
        )

    def test_previous_track_falls_back_to_playerctl(self):
        playerctl = _StubPlayerctl(state=_state(), action_ok=True)
        backend = HybridBackend(
            mpris=_StubMpris(state=_state(), action_ok=False),
            playerctl=playerctl,
            rhythmbox=_StubRhythmbox(state=_state(), action_ok=False),
        )
        assert (
            backend.previous_track(
                _state(player_bus_name="org.mpris.MediaPlayer2.spotify")
            )
            is True
        )

    def test_set_volume_uses_playerctl_global_fallback(self):
        class _FallbackPlayerctl(_StubPlayerctl):
            def __init__(self):
                super().__init__(state=_state(), action_ok=False)
                self.calls = []

            def set_volume(
                self,
                preferred: str | None,
                volume_percent: int,
                strict_preferred: bool = False,
            ) -> bool:
                _ = strict_preferred
                self.calls.append((preferred, volume_percent))
                return preferred is None

        playerctl = _FallbackPlayerctl()
        backend = HybridBackend(
            mpris=_StubMpris(state=_state(), action_ok=False),
            playerctl=playerctl,
            rhythmbox=_StubRhythmbox(state=_state(), action_ok=False),
        )

        assert (
            backend.set_volume(
                _state(player_bus_name="", player_name="Spotify"),
                65,
            )
            is True
        )
        assert playerctl.calls == [("Spotify", 65), (None, 65)]

    def test_poll_no_candidates_and_small_helpers(self):
        backend = HybridBackend(
            mpris=_StubMpris(state=unavailable_state()),
            playerctl=_StubPlayerctl(state=unavailable_state()),
            rhythmbox=_StubRhythmbox(state=unavailable_state()),
        )
        state = backend.poll()
        assert state.available is False
        assert backend._last_source == ""
        assert (
            backend._is_rhythmbox_state(
                _state(player_bus_name=music_state_mod._RB_SERVICE)
            )
            is True
        )
        assert backend._is_rhythmbox_hint("Rhythmbox") is True
        assert backend._is_rhythmbox_hint("") is False

    def test_transport_and_volume_unavailable_state_short_circuit(self):
        backend = HybridBackend(
            mpris=_StubMpris(state=unavailable_state(), action_ok=False),
            playerctl=_StubPlayerctl(state=unavailable_state(), action_ok=False),
            rhythmbox=_StubRhythmbox(state=unavailable_state(), action_ok=False),
        )
        assert backend.play_pause(unavailable_state()) is False
        assert backend.next_track(unavailable_state()) is False
        assert backend.previous_track(unavailable_state()) is False
        assert backend.set_volume(unavailable_state(), 10) is False

    def test_state_score_prefers_playing_metadata_and_continuity(self):
        backend = HybridBackend(
            mpris=_StubMpris(state=unavailable_state()),
            playerctl=_StubPlayerctl(state=unavailable_state()),
            rhythmbox=_StubRhythmbox(state=unavailable_state()),
        )
        backend._last_state = _state(player_bus_name="same")
        high = backend._state_score(
            "mpris-rhythmbox",
            _state(
                player_bus_name="same",
                playback_status="Playing",
                title="x",
            ),
        )
        low = backend._state_score(
            "playerctl",
            _state(
                player_bus_name="other",
                playback_status="Stopped",
                title="",
                artist="",
            ),
        )
        assert high > low


class TestMusicAdditionalBranches:
    def test_mpris_constructor_error_and_basic_wrappers(self, monkeypatch):
        monkeypatch.setattr(music_state_mod.GLib, "Error", Exception)
        monkeypatch.setattr(
            music_state_mod.Gio,
            "bus_get_sync",
            lambda *args, **kwargs: (_ for _ in ()).throw(Exception("dbus")),
        )
        backend = music_state_mod.MprisBackend()
        assert backend._bus is None
        assert backend._dbus_proxy is None

        backend = object.__new__(music_state_mod.MprisBackend)
        backend._last_active_bus_name = ""
        backend._bus = object()
        backend._dbus_proxy = None
        backend._player_proxies = {}
        backend._props_proxies = {}
        calls: list[str] = []
        backend._call_player_method = lambda player_bus_name, method: (
            calls.append(method) or True
        )  # type: ignore[method-assign]
        assert backend.play_pause("a") is True
        assert backend.next_track("a") is True
        assert backend.previous_track("a") is True
        assert calls == ["PlayPause", "Next", "Previous"]

    def test_mpris_select_fallback_and_empty_property_unpack(self):
        backend = object.__new__(music_state_mod.MprisBackend)
        backend._last_active_bus_name = "b"
        s1 = _state(player_bus_name="a", playback_status="Paused")
        s2 = _state(player_bus_name="b", playback_status="Stopped")
        assert backend._select_player([s1, s2]).player_bus_name == "b"
        backend._last_active_bus_name = "x"
        assert backend._select_player([s1, s2]).player_bus_name == "a"
        backend._get_property = lambda **kwargs: None  # type: ignore[method-assign]
        assert backend._player_display_name("custom.Player.Instance") == "Custom"

        class _Props:
            def call_sync(self, *_args, **_kwargs):
                class _Result:
                    def unpack(self):
                        return ()

                return _Result()

        backend._get_props_proxy = lambda bus_name: _Props()  # type: ignore[method-assign]
        assert (
            backend._get_property(
                bus_name="x",
                interface_name="i",
                property_name="p",
            )
            is None
        )

    def test_playerctl_constructor_and_public_methods(self, monkeypatch):
        monkeypatch.setattr(
            music_state_mod.shutil, "which", lambda cmd: "/usr/bin/playerctl"
        )
        backend = PlayerctlBackend()
        assert backend._binary == "/usr/bin/playerctl"

        backend._select_player = lambda **kwargs: "spotify"  # type: ignore[method-assign]
        backend._read_state = lambda player: _state(player_bus_name=player)  # type: ignore[method-assign]
        backend._run_action = lambda player, action: bool(player and action)  # type: ignore[method-assign]
        state = backend.get_state(preferred="spotify", strict_preferred=False)
        assert state.available is True
        assert backend._last_active_player == "spotify"
        assert backend.play_pause(preferred="spotify") is True
        assert backend.next_track(preferred="spotify") is True
        assert backend.previous_track(preferred="spotify") is True

    def test_playerctl_read_state_and_matching_edges(self):
        backend = object.__new__(PlayerctlBackend)
        backend._last_active_player = ""
        backend._binary = "playerctl"

        def fake_run(*, cmd, timeout):
            if cmd[-1] == "status":
                return "Playing"
            if "metadata" in cmd:
                return "Artist\tTitle\tAlbum\tart\turl\tPlayer\tplayer.desktop"
            if cmd[-1] == "volume":
                return "bad-float"
            return ""

        backend._run = fake_run  # type: ignore[method-assign]
        state = backend._read_state("spotify")
        assert state.available is True
        assert state.volume_percent == 0
        assert state.player_icon_name == "player"
        assert state.player_desktop_entry == "player.desktop"
        assert backend._run_action(None, "play-pause") is False
        assert backend._run_action("spotify", "play-pause") is True

        backend._list_players = list  # type: ignore[method-assign]
        assert backend._select_player() is None
        backend._list_players = lambda: ["vlc"]  # type: ignore[method-assign]
        backend._run = lambda **kwargs: None  # type: ignore[method-assign]
        assert (
            backend._select_player(preferred="unknown", strict_preferred=False) == "vlc"
        )
        assert backend._match_player_name(["vlc"], None) is None
        assert backend._match_player_name(["vlc"], "   ") is None
        assert (
            backend._match_player_name(["rhythmbox"], music_state_mod._RB_SERVICE)
            == "rhythmbox"
        )
        assert backend._match_player_name(["VLC"], "vlc") == "VLC"
        assert backend._match_player_name(["spotify"], "spotify") == "spotify"

    def test_playerctl_list_and_run_branches(self, monkeypatch):
        backend = object.__new__(PlayerctlBackend)
        backend._last_active_player = ""
        backend._binary = None
        assert backend._list_players() == []
        assert backend._run(["playerctl"], 1.0) is None

        backend._binary = "playerctl"
        backend._run = lambda **kwargs: None  # type: ignore[method-assign]
        assert backend._list_players() == []
        backend._run = music_state_mod.PlayerctlBackend._run.__get__(  # type: ignore[method-assign]
            backend,
            music_state_mod.PlayerctlBackend,
        )

        class _Proc:
            def __init__(self, code=0, stdout=""):
                self.returncode = code
                self.stdout = stdout

        monkeypatch.setattr(
            music_state_mod.subprocess,
            "run",
            lambda *args, **kwargs: _Proc(code=1, stdout=""),
        )
        assert backend._run(["playerctl", "-l"], 1.0) is None

    def test_rhythmbox_additional_method_branches(self, monkeypatch):
        backend = object.__new__(RhythmboxClientBackend)
        backend._binary = "rhythmbox-client"
        backend._gdbus_binary = None
        backend._settings = None
        calls: list[tuple[str, str]] = []
        backend._run_action = lambda action, gtk_action: (
            calls.append((action, gtk_action)) or True
        )  # type: ignore[method-assign]
        assert backend.play_pause() is True
        assert backend.next_track() is True
        assert backend.previous_track() is True
        assert calls == [
            ("--play-pause", "play"),
            ("--next", "play-next"),
            ("--previous", "play-previous"),
        ]

        backend._binary = None
        assert backend._read_volume_percent() == 0
        assert backend._run(["x"], timeout=1.0) is None
        assert backend._activate_gtk_action("play") is False

        backend = object.__new__(RhythmboxClientBackend)
        backend._binary = "rhythmbox-client"
        backend._gdbus_binary = "gdbus"
        backend._settings = None
        backend._run = lambda **kwargs: ""  # type: ignore[method-assign]
        assert backend._run_action("--next", gtk_action="play-next") is True

        class _Proc:
            def __init__(self, code):
                self.returncode = code

        monkeypatch.setattr(
            music_state_mod.subprocess,
            "run",
            lambda *args, **kwargs: _Proc(code=1),
        )
        assert backend._activate_gtk_action("play") is False

    def test_hybrid_poll_and_transport_fallback_paths(self):
        rb_mpris = _state(
            player_name="Rhythmbox",
            player_bus_name=music_state_mod._RB_MPRIS_SERVICE,
            playback_status="Playing",
        )
        mpris_state = _state(
            player_name="Spotify",
            player_bus_name="org.mpris.MediaPlayer2.spotify",
            playback_status="Paused",
        )

        class _MprisWithRb(_StubMpris):
            def get_state_for_bus_name(self, bus_name: str) -> MusicState:
                return rb_mpris

            def get_state(self) -> MusicState:
                return mpris_state

        backend = HybridBackend(
            mpris=_MprisWithRb(state=mpris_state, action_ok=True),
            playerctl=_StubPlayerctl(
                state=_state(player_name="playerctl"), action_ok=True
            ),
            rhythmbox=_StubRhythmbox(state=_state(player_name="rb"), action_ok=True),
        )
        selected = backend.poll()
        assert selected.player_bus_name == music_state_mod._RB_MPRIS_SERVICE

        assert (
            backend.play_pause(
                _state(
                    player_bus_name=music_state_mod._RB_SERVICE, player_name="Rhythmbox"
                )
            )
            is True
        )
        assert (
            backend.play_pause(
                _state(
                    player_bus_name="org.mpris.MediaPlayer2.spotify",
                    player_name="Spotify",
                )
            )
            is True
        )
        assert (
            backend.next_track(
                _state(
                    player_bus_name="org.mpris.MediaPlayer2.spotify",
                    player_name="Spotify",
                )
            )
            is True
        )
        assert (
            backend.previous_track(
                _state(
                    player_bus_name=music_state_mod._RB_SERVICE, player_name="Rhythmbox"
                )
            )
            is True
        )

        assert (
            backend.set_volume(
                _state(
                    player_bus_name=music_state_mod._RB_SERVICE, player_name="Rhythmbox"
                ),
                70,
            )
            is True
        )
        assert (
            backend.set_volume(
                _state(
                    player_bus_name="org.mpris.MediaPlayer2.spotify",
                    player_name="Spotify",
                ),
                70,
            )
            is True
        )
