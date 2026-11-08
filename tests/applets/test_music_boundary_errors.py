"""Media failures must leave artwork resolution usable."""

import json
import os
from unittest.mock import Mock

import pytest
from gi.repository import GdkPixbuf

from docking.applets.music.artwork import CoverArtResolver
from docking.applets.music.state import MusicState


def _cover(path, color):
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 4, 4)
    pixbuf.fill(color)
    pixbuf.savev(str(path), "png", [], [])
    return path


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX byte filenames have no Windows equivalent"
)
def test_artwork_uri_keeps_filesystem_filename_bytes(tmp_path):
    cover = _cover(tmp_path / os.fsdecode(b"cover-\xff.png"), 0x112233FF)

    image = CoverArtResolver().resolve(
        MusicState(available=True, art_url=cover.as_uri())
    )

    assert image is not None
    assert image.get_pixels()[:3] == b"\x11\x22\x33"


@pytest.mark.parametrize("art_url", ["file://[invalid", "file:relative.png", "remote"])
def test_unusable_artwork_uri_uses_the_tracks_local_cover(tmp_path, art_url):
    album = tmp_path / "album"
    album.mkdir()
    track = album / "song.mp3"
    track.write_bytes(b"music")
    _cover(album / "cover.png", 0x112233FF)
    if art_url == "remote":
        wrong_cover = _cover(tmp_path / "remote.png", 0x998877FF)
        art_url = "file://remote.example" + str(wrong_cover)

    image = CoverArtResolver().resolve(
        MusicState(available=True, art_url=art_url, track_url=track.as_uri())
    )

    assert image is not None
    assert image.get_pixels()[:3] == b"\x11\x22\x33"


def test_unreadable_album_directory_does_not_abort_artwork_resolution(tmp_path):
    album = tmp_path / "private"
    album.mkdir()
    album.chmod(0)
    try:
        state = MusicState(available=True, track_url=(album / "song.mp3").as_uri())
        assert CoverArtResolver().resolve(state) is None
    finally:
        album.chmod(0o700)


@pytest.mark.parametrize("payload", [None, [], {"results": 42}, {"results": [42]}])
def test_malformed_online_artwork_metadata_is_a_cache_miss(monkeypatch, payload):
    resolver = CoverArtResolver()
    monkeypatch.setattr(
        resolver, "_download_bytes", lambda **_kwargs: json.dumps(payload).encode()
    )

    assert (
        resolver.resolve(MusicState(available=True, artist="Artist", album="Album"))
        is None
    )


def test_native_artwork_bytes_are_decoded_and_changed_pixels_invalidate_cache(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from docking.applets.music.artwork import CoverArtResolver
    from docking.applets.music.state import MusicState

    first, second = tmp_path / "one.png", tmp_path / "two.png"
    _cover(first, 0x143C6EFF)
    _cover(second, 0xB42850FF)
    resolver = CoverArtResolver()
    remote = Mock(side_effect=AssertionError("Native artwork should need no lookup"))
    monkeypatch.setattr(resolver, "_lookup_online_cover_url", remote)
    state = MusicState(
        available=True,
        player_id="session",
        title="Same metadata",
        art_data=first.read_bytes(),
    )
    one = resolver.resolve(state)
    assert tuple(one.get_pixels()[:3]) == (20, 60, 110)
    assert resolver.resolve(state) is one
    two = resolver.resolve(replace(state, art_data=second.read_bytes()))
    assert tuple(two.get_pixels()[:3]) == (180, 40, 80)
    remote.assert_not_called()


def test_unicode_artwork_paths_and_file_uris_are_native(tmp_path):
    cover = _cover(tmp_path / "cover-ü-🎵.png", 0x112233FF)
    resolver = CoverArtResolver()
    for value in (str(cover), cover.as_posix(), cover.as_uri()):
        image = resolver._load_from_uri(value)
        assert image is not None
        assert image.get_pixels()[:3] == b"\x11\x22\x33"
