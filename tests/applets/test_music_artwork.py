"""Tests for music album-art resolution and cache behavior."""

from __future__ import annotations

import json
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from docking.applets.music.artwork import CoverArtResolver
from docking.applets.music.state import MusicState


def _pixbuf(size: int = 4) -> GdkPixbuf.Pixbuf:
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
    assert pixbuf is not None
    pixbuf.fill(0xFF3366FF)
    return pixbuf


def _state(**overrides: object) -> MusicState:
    base = MusicState(
        available=True,
        player_name="Player",
        player_bus_name="org.mpris.MediaPlayer2.player",
        playback_status="Playing",
        title="Track",
        artist="Artist",
        album="Album",
        volume_percent=50,
        can_play_pause=True,
        can_go_next=True,
        can_go_previous=True,
        art_url="https://example.com/cover.jpg",
        track_url="file:///tmp/song.mp3",
    )
    values = {field: getattr(base, field) for field in MusicState.__dataclass_fields__}
    values.update(overrides)
    return MusicState(**values)


class TestCoverArtResolver:
    def test_find_local_cover_case_insensitive(self, tmp_path: Path):
        resolver = CoverArtResolver()
        album_dir = tmp_path / "Album"
        album_dir.mkdir()
        track = album_dir / "song.mp3"
        track.write_bytes(b"track")
        cover = album_dir / "Cover.JPG"
        cover.write_bytes(b"cover")

        result = resolver._find_local_cover_path(track_url=track.as_posix())
        assert result == cover

    def test_uses_art_url_first(self, monkeypatch):
        resolver = CoverArtResolver()
        calls: list[str] = []

        monkeypatch.setattr(resolver, "_find_local_cover_path", lambda *_a, **_k: None)
        monkeypatch.setattr(
            resolver,
            "_lookup_online_cover_url",
            lambda *_a, **_k: "https://example.com/fallback.jpg",
        )

        def fake_load(uri: str):
            calls.append(uri)
            return _pixbuf()

        monkeypatch.setattr(resolver, "_load_from_uri", fake_load)
        result = resolver.resolve(_state())
        assert result is not None
        assert calls == ["https://example.com/cover.jpg"]

    def test_falls_back_to_local_cover(self, monkeypatch, tmp_path: Path):
        resolver = CoverArtResolver()
        local_cover = tmp_path / "cover.jpg"
        local_cover.write_bytes(b"fake")

        monkeypatch.setattr(resolver, "_load_from_uri", lambda *_a, **_k: None)
        monkeypatch.setattr(
            resolver,
            "_find_local_cover_path",
            lambda *_a, **_k: local_cover,
        )
        monkeypatch.setattr(resolver, "_load_from_path", lambda *_a, **_k: _pixbuf())
        monkeypatch.setattr(
            resolver, "_lookup_online_cover_url", lambda *_a, **_k: None
        )

        result = resolver.resolve(_state(art_url=""))
        assert result is not None

    def test_falls_back_to_online_lookup(self, monkeypatch):
        resolver = CoverArtResolver()
        calls: list[str] = []

        monkeypatch.setattr(resolver, "_load_from_path", lambda *_a, **_k: None)
        monkeypatch.setattr(resolver, "_find_local_cover_path", lambda *_a, **_k: None)
        monkeypatch.setattr(
            resolver,
            "_lookup_online_cover_url",
            lambda *_a, **_k: "https://example.com/online.jpg",
        )

        def fake_load(uri: str):
            calls.append(uri)
            if uri.endswith("cover.jpg"):
                return None
            return _pixbuf()

        monkeypatch.setattr(resolver, "_load_from_uri", fake_load)

        result = resolver.resolve(_state())
        assert result is not None
        assert calls == [
            "https://example.com/cover.jpg",
            "https://example.com/online.jpg",
        ]

    def test_cache_hit_skips_reloading(self, monkeypatch):
        resolver = CoverArtResolver()
        seen = {"count": 0}

        def fake_load(uri: str):
            _ = uri
            seen["count"] += 1
            return _pixbuf()

        monkeypatch.setattr(resolver, "_load_from_uri", fake_load)
        monkeypatch.setattr(resolver, "_find_local_cover_path", lambda *_a, **_k: None)
        monkeypatch.setattr(
            resolver, "_lookup_online_cover_url", lambda *_a, **_k: None
        )

        state = _state()
        first = resolver.resolve(state)
        second = resolver.resolve(state)
        assert first is not None
        assert second is first
        assert seen["count"] == 1

    def test_resolve_handles_empty_key_and_recent_miss(self, monkeypatch):
        resolver = CoverArtResolver()
        assert resolver.resolve(_state(available=False)) is None

        calls = {"count": 0}
        monkeypatch.setattr(
            resolver,
            "_resolve_uncached",
            lambda state: calls.__setitem__("count", calls["count"] + 1) or None,
        )
        monkeypatch.setattr(
            "docking.applets.music.artwork.time.monotonic", lambda: 100.0
        )
        state = _state(art_url="", track_url="")
        assert resolver.resolve(state) is None
        assert resolver.resolve(state) is None
        assert calls["count"] == 1

    def test_cache_key_and_path_parsing(self):
        resolver = CoverArtResolver()
        assert resolver._cache_key(_state(available=False)) == ""
        assert (
            resolver._cache_key(_state(player_name="", artist="", album="", title=""))
            != ""
        )
        assert resolver._path_from_uri_or_path("  ") is None
        assert resolver._path_from_uri_or_path("file:///tmp/a%20b.mp3") == Path(
            "/tmp/a b.mp3"
        )
        assert resolver._path_from_uri_or_path("/tmp/song.mp3") == Path("/tmp/song.mp3")
        assert resolver._path_from_uri_or_path("https://example.com/x") is None

    def test_find_local_cover_invalid_folder(self, tmp_path: Path):
        resolver = CoverArtResolver()
        track = tmp_path / "song.mp3"
        track.write_bytes(b"track")
        assert (
            resolver._find_local_cover_path(track_url=(tmp_path / "missing").as_posix())
            is None
        )

    def test_lookup_online_cover_url_branches(self, monkeypatch):
        resolver = CoverArtResolver()
        assert resolver._lookup_online_cover_url("", "", "") is None
        monkeypatch.setattr(
            resolver, "_download_bytes", lambda uri, require_image: None
        )
        assert resolver._lookup_online_cover_url("A", "B", "") is None

        monkeypatch.setattr(
            resolver,
            "_download_bytes",
            lambda uri, require_image: b"not-json",
        )
        assert resolver._lookup_online_cover_url("A", "B", "") is None

        monkeypatch.setattr(
            resolver,
            "_download_bytes",
            lambda uri, require_image: json.dumps({"results": []}).encode("utf-8"),
        )
        assert resolver._lookup_online_cover_url("A", "B", "") is None

        monkeypatch.setattr(
            resolver,
            "_download_bytes",
            lambda uri, require_image: json.dumps({"results": [{}]}).encode("utf-8"),
        )
        assert resolver._lookup_online_cover_url("A", "B", "") is None

        monkeypatch.setattr(
            resolver,
            "_download_bytes",
            lambda uri, require_image: json.dumps(
                {"results": [{"artworkUrl100": "https://img/100x100bb.jpg"}]}
            ).encode("utf-8"),
        )
        assert (
            resolver._lookup_online_cover_url("A", "B", "")
            == "https://img/600x600bb.jpg"
        )

    def test_load_from_path_and_uri_variants(self, monkeypatch, tmp_path: Path):
        resolver = CoverArtResolver()
        image = tmp_path / "cover.png"
        image.write_bytes(b"not-image")
        assert resolver._load_from_path(image) is None
        assert resolver._load_from_uri("custom://cover") is None

        monkeypatch.setattr(resolver, "_load_from_path", lambda path: _pixbuf())
        assert resolver._load_from_uri(image.as_posix()) is not None
        assert resolver._load_from_uri(f"file://{image}") is not None

        monkeypatch.setattr(
            resolver, "_download_bytes", lambda uri, require_image: b"abc"
        )
        monkeypatch.setattr(resolver, "_pixbuf_from_bytes", lambda payload: _pixbuf())
        assert resolver._load_from_uri("https://example.com/art.jpg") is not None

    def test_download_bytes_limits_and_errors(self, monkeypatch):
        resolver = CoverArtResolver()

        class _Headers:
            def __init__(self, content_type: str):
                self._content_type = content_type

            def get_content_type(self):
                return self._content_type

        class _Response:
            def __init__(self, chunks: list[bytes], content_type: str):
                self._chunks = chunks
                self._index = 0
                self.headers = _Headers(content_type)

            def read(self, _size: int):
                if self._index >= len(self._chunks):
                    return b""
                chunk = self._chunks[self._index]
                self._index += 1
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr(
            "docking.applets.music.artwork.urllib.request.urlopen",
            lambda request, timeout: _Response([b"abc"], "text/plain"),
        )
        assert resolver._download_bytes("https://x", require_image=True) is None

        monkeypatch.setattr(
            "docking.applets.music.artwork.urllib.request.urlopen",
            lambda request, timeout: _Response(
                [b"a" * (5 * 1024 * 1024)], "image/jpeg"
            ),
        )
        assert resolver._download_bytes("https://x", require_image=True) is None

        monkeypatch.setattr(
            "docking.applets.music.artwork.urllib.request.urlopen",
            lambda request, timeout: _Response([b"a", b"b"], "image/jpeg"),
        )
        assert resolver._download_bytes("https://x", require_image=True) == b"ab"

        def fail_open(*args, **kwargs):
            raise OSError("offline")

        monkeypatch.setattr(
            "docking.applets.music.artwork.urllib.request.urlopen",
            fail_open,
        )
        assert resolver._download_bytes("https://x", require_image=False) is None

    def test_pixbuf_from_bytes_and_cache_eviction(self, monkeypatch):
        resolver = CoverArtResolver(max_entries=1, max_bytes=10_000)
        assert resolver._pixbuf_from_bytes(b"bad") is None

        p1 = _pixbuf(size=2)
        p2 = _pixbuf(size=3)
        resolver._insert_cache("k1", p1)
        resolver._insert_cache("k2", p2)
        assert "k1" not in resolver._cache
        assert "k2" in resolver._cache

        class _PB:
            def get_width(self):
                return 2

            def get_height(self):
                return 3

            def get_n_channels(self):
                return 0

        assert resolver._pixbuf_size_bytes(_PB()) == 24
