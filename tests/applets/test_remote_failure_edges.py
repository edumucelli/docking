"""Protocol failures must recover without accepting unusable links or retaining misses forever."""

import http.client
import io
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from gi.repository import GdkPixbuf

from docking.applets.dragshare.state import UploadError, upload_file
from docking.applets.music import artwork as artwork_mod
from docking.applets.music.artwork import CoverArtResolver
from docking.applets.music.state import MusicState
from docking.applets.urlshortener import state as shortener_mod


class _Response(io.BytesIO):
    def getcode(self):
        return 200


def test_malformed_http_status_does_not_abort_artwork_resolution(monkeypatch):
    class Socket:
        def makefile(self, *_args, **_kwargs):
            return io.BytesIO(b"not an HTTP response\r\n\r\n")

    def open_response(*_args, **_kwargs):
        response = http.client.HTTPResponse(Socket())
        response.begin()
        return response

    monkeypatch.setattr(artwork_mod.urllib.request, "urlopen", open_response)
    state = MusicState(available=True, art_url="http://127.0.0.1/cover.png")

    assert CoverArtResolver().resolve(state) is None


def test_control_character_in_artwork_url_is_rejected_without_crashing():
    state = MusicState(available=True, art_url="http://127.0.0.1:1/bad\x00path")

    assert CoverArtResolver().resolve(state) is None


def test_deeply_nested_artwork_json_is_a_miss(monkeypatch):
    payload = b'{"results": [], "extra":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"
    resolver = CoverArtResolver()
    monkeypatch.setattr(resolver, "_download_bytes", lambda **_kwargs: payload)

    assert (
        resolver.resolve(MusicState(available=True, artist="Artist", album="Album"))
        is None
    )


def test_failed_artwork_lookups_keep_retry_cache_bounded(monkeypatch):
    resolver = CoverArtResolver(max_entries=4)
    monkeypatch.setattr(resolver, "_download_bytes", lambda **_kwargs: None)
    for index in range(100):
        assert (
            resolver.resolve(MusicState(available=True, title=f"Track {index}")) is None
        )

    assert len(resolver._recent_misses) <= 4


@pytest.mark.parametrize(
    "url",
    [
        "https://",
        "http://",
        "https://example.test:bad/file",
        "https://example.test:65536/file",
        "https://example.test/file\nextra",
        "https://example.test/\ud800",
    ],
)
def test_upload_response_cannot_claim_success_for_invalid_url(tmp_path, url):
    path = tmp_path / "selected.txt"
    path.write_text("selected file")

    def opener(_request, *, timeout):
        return _Response(json.dumps({"data": {"url": url}}).encode())

    with pytest.raises(UploadError):
        upload_file(path, opener=opener)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test:bad/file",
        "https://example.test:65536/file",
    ],
)
def test_shortener_response_rejects_invalid_port(monkeypatch, url):
    monkeypatch.setattr(
        shortener_mod.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(url.encode()),
    )

    assert shortener_mod.shorten_url("https://example.test/long").startswith("Error:")


def test_frequent_artwork_misses_keep_their_retry_cooldown(monkeypatch):
    resolver = CoverArtResolver(max_entries=2)
    requests = []

    def download(*, uri, require_image):
        requests.append(uri)
        return

    monkeypatch.setattr(resolver, "_download_bytes", download)
    for title in ("A", "B", "A", "C", "A"):
        assert resolver.resolve(MusicState(available=True, title=title)) is None

    assert len(requests) == 3


def test_simultaneous_artwork_results_do_not_double_count_cache_bytes(monkeypatch):
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 4, 4)
    assert pixbuf is not None
    resolver = CoverArtResolver(max_entries=1, max_bytes=48)
    barrier = Barrier(2)
    requests = []

    def fetch(state):
        requests.append(state)
        if len(requests) <= 2:
            barrier.wait(timeout=3)
        return pixbuf

    monkeypatch.setattr(resolver, "_resolve_uncached", fetch)
    state = MusicState(available=True, title="Shared artwork")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(resolver.resolve, (state, state)))

    assert all(result is pixbuf for result in results)
    assert resolver.resolve(state) is pixbuf
    assert len(requests) == 2
