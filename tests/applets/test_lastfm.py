"""Tests for the Last.fm applet."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import docking.applets.lastfm.applet as lastfm_applet_mod
from docking.applets.lastfm.applet import LastfmApplet
from docking.applets.lastfm.render import (
    pixbuf_from_bytes,
    render_default_icon,
    round_pixbuf_corners,
)
from docking.applets.lastfm.state import (
    DEFAULT_MAX_ENTRIES,
    DEFAULT_SERVICE,
    LASTFM_API_BASE,
    LASTFM_SERVICE,
    LIBREFM_API_BASE,
    LIBREFM_SERVICE,
    LIBREFM_USER_URL_BASE,
    MAX_MAX_ENTRIES,
    MIN_MAX_ENTRIES,
    NOT_FOUND_IMAGE_HASH,
    ImageCache,
    LastfmPrefs,
    PlayedTrack,
    best_image_url,
    build_recent_tracks_url,
    format_relative_time,
    is_placeholder_image,
    normalize_service,
    parse_recent_tracks,
    prefs_from_mapping,
    prefs_payload,
    profile_url,
    tooltip_for,
)
from docking.core.config import Config


class _ImmediateWorker:
    """Test double: runs worker tasks synchronously."""

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


# ---------- prefs ----------


class TestPrefsFromMapping:
    def test_empty_mapping_returns_defaults(self):
        prefs = prefs_from_mapping({})
        assert prefs.api_key == ""
        assert prefs.username == ""
        assert prefs.max_entries == DEFAULT_MAX_ENTRIES
        assert prefs.rounded_corners is False
        assert prefs.service == DEFAULT_SERVICE
        assert prefs.is_configured is False

    def test_none_mapping_returns_defaults(self):
        assert prefs_from_mapping(None) == LastfmPrefs()

    def test_full_mapping_populates_fields(self):
        prefs = prefs_from_mapping(
            {
                "api_key": "abcd",
                "username": "ed",
                "max_entries": 5,
                "rounded_corners": True,
            }
        )
        assert prefs.api_key == "abcd"
        assert prefs.username == "ed"
        assert prefs.max_entries == 5
        assert prefs.rounded_corners is True
        assert prefs.is_configured is True

    def test_strips_whitespace_from_credentials(self):
        prefs = prefs_from_mapping({"api_key": "  k  ", "username": " ed "})
        assert prefs.api_key == "k"
        assert prefs.username == "ed"

    def test_clamps_max_entries_to_supported_range(self):
        assert prefs_from_mapping({"max_entries": 0}).max_entries == MIN_MAX_ENTRIES
        assert prefs_from_mapping({"max_entries": 999}).max_entries == MAX_MAX_ENTRIES

    def test_invalid_max_entries_falls_back_to_default(self):
        assert (
            prefs_from_mapping({"max_entries": "nope"}).max_entries
            == DEFAULT_MAX_ENTRIES
        )

    def test_payload_round_trip(self):
        original = LastfmPrefs(
            api_key="k",
            username="u",
            max_entries=7,
            rounded_corners=True,
            service=LIBREFM_SERVICE,
        )
        assert prefs_from_mapping(prefs_payload(original)) == original

    def test_service_normalization(self):
        assert prefs_from_mapping({"service": "  LibreFM  "}).service == LIBREFM_SERVICE
        assert prefs_from_mapping({"service": "lastfm"}).service == LASTFM_SERVICE
        assert prefs_from_mapping({"service": "spotify"}).service == DEFAULT_SERVICE
        assert prefs_from_mapping({"service": 123}).service == DEFAULT_SERVICE

    def test_librefm_configured_with_username_only(self):
        prefs = LastfmPrefs(username="ed", service=LIBREFM_SERVICE)
        assert prefs.is_configured is True

    def test_lastfm_requires_api_key(self):
        assert LastfmPrefs(username="ed", service=LASTFM_SERVICE).is_configured is False


# ---------- URL building ----------


class TestBuildRecentTracksUrl:
    def test_includes_required_params(self):
        url = build_recent_tracks_url(api_key="K", username="ed", limit=5)
        assert url.startswith(LASTFM_API_BASE)
        assert "method=user.getrecenttracks" in url
        assert "user=ed" in url
        assert "api_key=K" in url
        assert "format=json" in url
        assert "limit=5" in url
        assert "extended=1" in url

    def test_url_encodes_username(self):
        url = build_recent_tracks_url(api_key="K", username="A B", limit=1)
        assert "A+B" in url or "A%20B" in url

    def test_clamps_limit_to_at_least_one(self):
        url = build_recent_tracks_url(api_key="K", username="ed", limit=0)
        assert "limit=1" in url

    def test_profile_url_quotes_username(self):
        assert profile_url(LASTFM_SERVICE, "A B").endswith("A+B")

    def test_librefm_url_uses_libre_fm_host(self):
        url = build_recent_tracks_url(
            api_key="", username="ed", limit=3, service=LIBREFM_SERVICE
        )
        assert url.startswith(LIBREFM_API_BASE)
        # Read methods on Libre.fm don't need an API key; ours stays out.
        assert "api_key" not in url
        assert "user=ed" in url

    def test_omits_empty_api_key_for_lastfm_too(self):
        url = build_recent_tracks_url(api_key="", username="ed", limit=1)
        assert "api_key" not in url

    def test_librefm_profile_url(self):
        assert profile_url(LIBREFM_SERVICE, "ed") == f"{LIBREFM_USER_URL_BASE}ed"

    def test_normalize_service_accepts_known_values(self):
        assert normalize_service("lastfm") == LASTFM_SERVICE
        assert normalize_service("librefm") == LIBREFM_SERVICE
        assert normalize_service("bogus") == DEFAULT_SERVICE
        assert normalize_service(None) == DEFAULT_SERVICE


# ---------- JSON parsing ----------


def _make_track_node(
    *,
    name="Song",
    artist="Artist",
    album="Album",
    url="https://last.fm/track",
    images=None,
    timestamp=None,
    now_playing=False,
    loved="0",
):
    node: dict = {
        "name": name,
        "url": url,
        "loved": loved,
        "artist": {"name": artist, "mbid": ""},
        "album": {"#text": album, "mbid": ""},
        "image": [
            {"size": s, "#text": (images or {}).get(s, "")}
            for s in ("small", "medium", "large", "extralarge")
        ],
    }
    if now_playing:
        node["@attr"] = {"nowplaying": "true"}
    if timestamp is not None:
        node["date"] = {"uts": str(timestamp), "#text": "now"}
    return node


class TestParseRecentTracks:
    def test_parses_a_single_played_track(self):
        payload = {
            "recenttracks": {
                "track": _make_track_node(
                    images={"extralarge": "https://img/xl.png"},
                    timestamp=1_700_000_000,
                )
            }
        }
        tracks = parse_recent_tracks(payload, limit=5)
        assert len(tracks) == 1
        t = tracks[0]
        assert t.track_name == "Song"
        assert t.artist == "Artist"
        assert t.album == "Album"
        assert t.is_now_playing is False
        assert t.is_loved is False
        assert t.timestamp == 1_700_000_000
        assert t.image_urls["extralarge"] == "https://img/xl.png"

    def test_parses_array_of_tracks(self):
        payload = {
            "recenttracks": {
                "track": [
                    _make_track_node(name=f"T{i}", timestamp=1_700_000_000 - i)
                    for i in range(3)
                ]
            }
        }
        assert [t.track_name for t in parse_recent_tracks(payload, limit=10)] == [
            "T0",
            "T1",
            "T2",
        ]

    def test_now_playing_flag_set(self):
        payload = {
            "recenttracks": {
                "track": _make_track_node(now_playing=True),
            }
        }
        tracks = parse_recent_tracks(payload, limit=5)
        assert tracks[0].is_now_playing is True
        assert tracks[0].timestamp is None

    def test_now_playing_filter_skips_now_playing_when_disabled(self):
        payload = {
            "recenttracks": {
                "track": [
                    _make_track_node(now_playing=True),
                    _make_track_node(name="Past", timestamp=100),
                ]
            }
        }
        tracks = parse_recent_tracks(payload, limit=5, now_playing=False)
        assert [t.track_name for t in tracks] == ["Past"]

    def test_returns_empty_list_on_api_error_payload(self):
        assert parse_recent_tracks({"error": 6, "message": "User not found"}, 5) == []

    def test_handles_missing_recenttracks_key(self):
        assert parse_recent_tracks({}, 5) == []

    def test_handles_missing_track_member(self):
        assert parse_recent_tracks({"recenttracks": {}}, 5) == []

    def test_respects_limit_even_when_api_returns_extra(self):
        """Last.fm sometimes returns ``limit+1`` items because of the
        now-playing prepend. Parser must not exceed ``limit``."""
        payload = {
            "recenttracks": {
                "track": [_make_track_node(name=f"T{i}") for i in range(6)],
            }
        }
        tracks = parse_recent_tracks(payload, limit=3)
        assert len(tracks) == 3

    def test_loved_flag_propagates(self):
        payload = {
            "recenttracks": {
                "track": _make_track_node(loved="1", timestamp=1),
            }
        }
        assert parse_recent_tracks(payload, limit=1)[0].is_loved is True

    def test_artist_name_falls_back_to_text(self):
        payload = {
            "recenttracks": {
                "track": {
                    "name": "S",
                    "url": "u",
                    "artist": {"#text": "TextArtist"},
                    "image": [],
                }
            }
        }
        assert parse_recent_tracks(payload, limit=1)[0].artist == "TextArtist"

    def test_drops_track_with_empty_name(self):
        payload = {
            "recenttracks": {
                "track": [
                    {"name": "", "url": "u", "image": []},
                    _make_track_node(name="Real", timestamp=1),
                ]
            }
        }
        assert [t.track_name for t in parse_recent_tracks(payload, limit=5)] == ["Real"]


# ---------- best_image_url + placeholder detection ----------


class TestImageUrls:
    def test_returns_extralarge_when_available(self):
        track = PlayedTrack(
            track_name="t",
            artist="a",
            album="",
            track_url="",
            image_urls={
                "small": "s",
                "medium": "m",
                "large": "l",
                "extralarge": "xl",
            },
            timestamp=None,
            is_now_playing=False,
            is_loved=False,
        )
        assert best_image_url(track) == "xl"

    def test_falls_back_to_smaller_sizes(self):
        track = PlayedTrack(
            track_name="t",
            artist="a",
            album="",
            track_url="",
            image_urls={"medium": "m", "small": "s"},
            timestamp=None,
            is_now_playing=False,
            is_loved=False,
        )
        assert best_image_url(track) == "m"

    def test_empty_when_no_images(self):
        track = PlayedTrack(
            track_name="t",
            artist="a",
            album="",
            track_url="",
            image_urls={},
            timestamp=None,
            is_now_playing=False,
            is_loved=False,
        )
        assert best_image_url(track) == ""

    def test_placeholder_detected_by_hash(self):
        assert is_placeholder_image(f"https://x/{NOT_FOUND_IMAGE_HASH}.png") is True
        assert is_placeholder_image("https://x/real.png") is False


# ---------- relative time ----------


class TestFormatRelativeTime:
    BASE = 1_700_000_000  # arbitrary Unix timestamp in 2023

    def test_returns_empty_for_no_timestamp(self):
        assert format_relative_time(None) == ""
        assert format_relative_time(0) == ""

    def test_just_now(self):
        assert format_relative_time(self.BASE, now=self.BASE + 10) == "Just now"

    def test_minutes_ago_pluralizes(self):
        assert format_relative_time(self.BASE, now=self.BASE + 60) == "1 minute ago"
        assert format_relative_time(self.BASE, now=self.BASE + 300) == "5 minutes ago"

    def test_hours_days_weeks(self):
        assert (
            format_relative_time(self.BASE, now=self.BASE + 3600 * 5) == "5 hours ago"
        )
        assert (
            format_relative_time(self.BASE, now=self.BASE + 86400 * 3) == "3 days ago"
        )
        assert (
            format_relative_time(self.BASE, now=self.BASE + 86400 * 14) == "2 weeks ago"
        )

    def test_months_and_years(self):
        assert (
            format_relative_time(self.BASE, now=self.BASE + 86400 * 90)
            == "3 months ago"
        )
        assert (
            format_relative_time(self.BASE, now=self.BASE + 86400 * 800)
            == "2 years ago"
        )


# ---------- tooltip ----------


class TestTooltip:
    def test_not_configured(self):
        assert tooltip_for(LastfmPrefs(), None) == "Last.fm: not configured"

    def test_not_configured_uses_service_label(self):
        prefs = LastfmPrefs(service=LIBREFM_SERVICE)
        assert tooltip_for(prefs, None) == "Libre.fm: not configured"

    def test_configured_no_track(self):
        prefs = LastfmPrefs(api_key="k", username="ed")
        assert tooltip_for(prefs, None) == "Last.fm: ed"

    def test_configured_librefm_no_track(self):
        prefs = LastfmPrefs(username="ed", service=LIBREFM_SERVICE)
        assert tooltip_for(prefs, None) == "Libre.fm: ed"

    def test_now_playing_uses_music_symbol(self):
        prefs = LastfmPrefs(api_key="k", username="ed")
        track = PlayedTrack(
            track_name="Song",
            artist="Artist",
            album="",
            track_url="",
            image_urls={},
            timestamp=None,
            is_now_playing=True,
            is_loved=False,
        )
        assert "♪" in tooltip_for(prefs, track)

    def test_past_track_uses_dash(self):
        prefs = LastfmPrefs(api_key="k", username="ed")
        track = PlayedTrack(
            track_name="Song",
            artist="Artist",
            album="",
            track_url="",
            image_urls={},
            timestamp=1,
            is_now_playing=False,
            is_loved=False,
        )
        assert tooltip_for(prefs, track) == "Artist - Song"


# ---------- image cache ----------


class TestImageCache:
    def test_capacity_is_three_times_max_entries(self):
        cache = ImageCache(max_entries=4)
        assert cache.capacity == 12

    def test_set_and_get_round_trip(self):
        cache = ImageCache(max_entries=2)
        cache.set("u", b"data")
        assert cache.get("u") == b"data"

    def test_clears_when_full(self):
        cache = ImageCache(max_entries=1)  # capacity 3
        cache.set("a", b"1")
        cache.set("b", b"2")
        cache.set("c", b"3")
        cache.set("d", b"4")  # triggers clear; only "d" remains
        assert cache.get("a") is None
        assert cache.get("d") == b"4"

    def test_resize_smaller_clears_when_over_new_capacity(self):
        cache = ImageCache(max_entries=10)
        for i in range(5):
            cache.set(f"u{i}", b"x")
        cache.resize(1)  # capacity now 3, holding 5 -> clear
        assert cache._entries == {}


# ---------- rendering helpers ----------


class TestRenderHelpers:
    def test_default_icon_returns_pixbuf(self):
        pixbuf = render_default_icon(size=48)
        assert pixbuf is not None
        assert pixbuf.get_width() == 48
        assert pixbuf.get_height() == 48

    def test_pixbuf_from_bytes_returns_none_for_empty(self):
        assert pixbuf_from_bytes(b"", 32) is None

    def test_pixbuf_from_bytes_returns_none_for_garbage(self):
        assert pixbuf_from_bytes(b"not an image", 32) is None

    def test_round_pixbuf_corners_preserves_dimensions(self):
        pixbuf = render_default_icon(size=64)
        rounded = round_pixbuf_corners(pixbuf)
        assert rounded.get_width() == pixbuf.get_width()
        assert rounded.get_height() == pixbuf.get_height()


# ---------- applet integration ----------


@pytest.fixture
def empty_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return Config()


@pytest.fixture
def configured_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    cfg = Config()
    cfg.applet_prefs["lastfm"] = {
        "api_key": "K",
        "username": "ed",
        "max_entries": 3,
        "rounded_corners": False,
    }
    return cfg


def _make_applet(monkeypatch, config, worker=_ImmediateWorker):
    monkeypatch.setattr(lastfm_applet_mod, "BackgroundWorker", worker)
    return LastfmApplet(icon_size=48, config=config)


class TestLastfmAppletLifecycle:
    def test_unconfigured_applet_shows_default_icon(self, monkeypatch, empty_config):
        applet = _make_applet(monkeypatch, empty_config)
        assert applet.item.name == "Last.fm: not configured"
        assert applet.item.icon is not None  # default icon rendered

    def test_unconfigured_applet_skips_fetch(self, monkeypatch, empty_config):
        called = {"n": 0}

        def fake_get_json(_url):
            called["n"] += 1
            return {}

        monkeypatch.setattr(lastfm_applet_mod, "http_get_json", fake_get_json)
        applet = _make_applet(monkeypatch, empty_config)
        applet._fetch_async()
        assert called["n"] == 0

    def test_fetch_populates_tracks_and_tooltip(self, monkeypatch, configured_config):
        payload = {
            "recenttracks": {
                "track": [
                    _make_track_node(name="Now", now_playing=True),
                    _make_track_node(name="Past", timestamp=1_700_000_000),
                ]
            }
        }
        monkeypatch.setattr(lastfm_applet_mod, "http_get_json", lambda _url: payload)
        monkeypatch.setattr(lastfm_applet_mod, "http_get_bytes", lambda _url, **_k: b"")
        applet = _make_applet(monkeypatch, configured_config)
        applet._fetch_async()
        assert [t.track_name for t in applet._tracks] == ["Now", "Past"]
        assert "Now" in applet.item.name

    def test_fetch_error_recorded(self, monkeypatch, configured_config):
        def raise_oserror(_url):
            raise OSError("network down")

        monkeypatch.setattr(lastfm_applet_mod, "http_get_json", raise_oserror)
        applet = _make_applet(monkeypatch, configured_config)
        applet._fetch_async()
        assert "network down" in applet._error
        assert applet._tracks == []

    def test_apply_prefs_saves_and_refetches(self, monkeypatch, empty_config):
        applet = _make_applet(monkeypatch, empty_config)
        fetched: list[str] = []

        def fake_get_json(url):
            fetched.append(url)
            return {"recenttracks": {"track": []}}

        monkeypatch.setattr(lastfm_applet_mod, "http_get_json", fake_get_json)
        applet._apply_new_prefs(
            api_key="K", username="ed", max_entries=4, rounded_corners=True
        )
        assert applet._prefs.is_configured is True
        assert empty_config.applet_prefs["lastfm"]["username"] == "ed"
        assert empty_config.applet_prefs["lastfm"]["max_entries"] == 4
        assert fetched, "expected an HTTP fetch after credentials applied"

    def test_switching_service_persists_and_uses_libre_fm_host(
        self, monkeypatch, empty_config
    ):
        fetched: list[str] = []

        def fake_get_json(url):
            fetched.append(url)
            return {"recenttracks": {"track": []}}

        monkeypatch.setattr(lastfm_applet_mod, "http_get_json", fake_get_json)
        applet = _make_applet(monkeypatch, empty_config)
        applet._apply_new_prefs(
            api_key="",
            username="ed",
            max_entries=3,
            rounded_corners=False,
            service=LIBREFM_SERVICE,
        )
        assert applet._prefs.service == LIBREFM_SERVICE
        assert applet._prefs.is_configured is True
        assert empty_config.applet_prefs["lastfm"]["service"] == LIBREFM_SERVICE
        assert fetched and fetched[-1].startswith(LIBREFM_API_BASE)

    def test_changing_credentials_clears_track_cache(
        self, monkeypatch, configured_config
    ):
        payload = {
            "recenttracks": {
                "track": _make_track_node(name="Old", timestamp=1),
            }
        }
        monkeypatch.setattr(lastfm_applet_mod, "http_get_json", lambda _url: payload)
        monkeypatch.setattr(lastfm_applet_mod, "http_get_bytes", lambda _url, **_k: b"")
        applet = _make_applet(monkeypatch, configured_config)
        applet._fetch_async()
        assert applet._tracks
        # Now switch user; tracks should be wiped before the next fetch.
        with patch.object(applet, "_fetch_async"):
            applet._apply_new_prefs(
                api_key="K2",
                username="other",
                max_entries=3,
                rounded_corners=False,
            )
        assert applet._tracks == []
