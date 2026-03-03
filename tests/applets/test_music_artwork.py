"""Tests for music album-art resolution and cache behavior."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

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
