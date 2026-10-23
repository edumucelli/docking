"""Media failures must leave artwork resolution usable."""

import json
import os

import pytest
from gi.repository import GdkPixbuf

from docking.applets.music.artwork import CoverArtResolver
from docking.applets.music.state import MusicState


def _cover(path, color):
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 4, 4)
    pixbuf.fill(color)
    pixbuf.savev(str(path), "png", [], [])
    return path


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
