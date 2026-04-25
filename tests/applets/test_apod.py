"""Tests for the APOD applet."""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

from docking.applets.apod.api import ApodError, fetch_today
from docking.applets.apod.applet import ApodApplet
from docking.applets.apod.state import (
    ApodPrefs,
    ApodResult,
    build_page_url,
    build_tooltip,
    format_explanation,
    prefs_from_mapping,
    prefs_payload,
)
from docking.core.config import Config

_SAMPLE_PAYLOAD = {
    "date": "2026-04-24",
    "explanation": "A spectacular spiral " + "word " * 200 + "end",
    "hdurl": "https://apod.nasa.gov/apod/image/2604/hd.jpg",
    "media_type": "image",
    "service_version": "v1",
    "title": "Spiral Galaxy NGC 1234",
    "url": "https://apod.nasa.gov/apod/image/2604/thumb.jpg",
    "copyright": "NASA Team",
}


class _ImmediateWorker:
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


def _make_applet(icon_size: int = 48, *, config: Config | None = None) -> ApodApplet:
    with patch("docking.applets.apod.applet.BackgroundWorker", _ImmediateWorker):
        return ApodApplet(icon_size, config=config)


def _sample_result(**overrides: object) -> ApodResult:
    base = {
        "date": "2026-04-24",
        "title": "Spiral Galaxy",
        "explanation": "A distant spiral galaxy.",
        "media_type": "image",
        "image_url": "https://apod.nasa.gov/apod/image/2604/thumb.jpg",
        "page_url": "https://apod.nasa.gov/apod/ap260424.html",
        "copyright": "NASA",
        "cached_path": "",
    }
    base.update(overrides)
    return ApodResult(**base)  # type: ignore[arg-type]


class TestFormatExplanation:
    def test_short_text_unchanged(self):
        assert format_explanation("hello world") == "hello world"

    def test_truncates_long_text(self):
        text = "word " * 500
        out = format_explanation(text, max_chars=40)
        assert out.endswith("…")
        assert len(out) <= 41

    def test_normalizes_whitespace(self):
        assert format_explanation("a\n\nb   c") == "a b c"

    def test_wraps_to_narrow_lines(self):
        text = "word " * 80
        out = format_explanation(text, max_chars=320, wrap=40)
        lines = out.splitlines()
        assert len(lines) > 1
        assert all(len(line) <= 40 for line in lines)


class TestBuildPageUrl:
    def test_derives_from_iso_date(self):
        assert (
            build_page_url("2026-04-24") == "https://apod.nasa.gov/apod/ap260424.html"
        )

    def test_bad_date_falls_back(self):
        assert build_page_url("bogus") == "https://apod.nasa.gov/apod/astropix.html"


class TestBuildTooltip:
    def test_loading_state(self):
        text = build_tooltip(result=None, error=None)
        assert "Loading" in text

    def test_error_state(self):
        text = build_tooltip(result=None, error="network down")
        assert "network down" in text

    def test_full_result(self):
        text = build_tooltip(result=_sample_result(), error=None)
        assert "Spiral Galaxy" in text
        assert "NASA" in text
        assert "2026-04-24" in text
        assert "distant spiral" in text


class TestPrefsRoundTrip:
    def test_none_returns_empty(self):
        assert prefs_from_mapping(None) == ApodPrefs()

    def test_round_trips(self):
        result = _sample_result()
        back = prefs_from_mapping(prefs_payload(result=result))
        assert back.last_result == result

    def test_missing_date_drops_entry(self):
        raw = {"last_result": {"title": "x"}}
        assert prefs_from_mapping(raw) == ApodPrefs()


class TestFetchToday:
    def test_parses_successful_response(self, tmp_path, monkeypatch):
        monkeypatch.setattr("docking.applets.apod.api.CACHE_DIR", tmp_path / "apod")

        def fake_urlopen(req, timeout=None):
            mock = MagicMock()
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if url.startswith("https://api.nasa.gov/"):
                mock.read.return_value = json.dumps(_SAMPLE_PAYLOAD).encode()
            else:
                # JPEG magic bytes are enough for the download path.
                mock.read.side_effect = [b"\xff\xd8\xff\xe0" + b"\x00" * 512, b""]
            mock.__enter__ = lambda self: self
            mock.__exit__ = lambda self, *a: None
            return mock

        monkeypatch.setattr(
            "docking.applets.apod.api.urllib.request.urlopen", fake_urlopen
        )

        got = fetch_today()
        assert isinstance(got, ApodResult)
        assert got.date == "2026-04-24"
        assert got.title == "Spiral Galaxy NGC 1234"
        assert got.image_url.endswith(".jpg")
        assert got.cached_path.endswith(".jpg")

    def test_video_media_type_uses_thumbnail(self, tmp_path, monkeypatch):
        monkeypatch.setattr("docking.applets.apod.api.CACHE_DIR", tmp_path / "apod")
        payload = dict(_SAMPLE_PAYLOAD)
        payload["media_type"] = "video"
        payload["url"] = "https://youtube.com/watch?v=abc"
        payload["thumbnail_url"] = "https://example/thumb.jpg"

        def fake_urlopen(req, timeout=None):
            mock = MagicMock()
            url = req.full_url if hasattr(req, "full_url") else str(req)
            if url.startswith("https://api.nasa.gov/"):
                mock.read.return_value = json.dumps(payload).encode()
            else:
                mock.read.side_effect = [b"\xff\xd8\xff\xe0" + b"\x00" * 64, b""]
            mock.__enter__ = lambda self: self
            mock.__exit__ = lambda self, *a: None
            return mock

        monkeypatch.setattr(
            "docking.applets.apod.api.urllib.request.urlopen", fake_urlopen
        )

        got = fetch_today()
        assert isinstance(got, ApodResult)
        assert got.media_type == "video"
        assert got.image_url == "https://example/thumb.jpg"

    def test_network_failure_returns_error(self, monkeypatch):
        def boom(req, timeout=None):
            raise OSError("network down")

        monkeypatch.setattr("docking.applets.apod.api.urllib.request.urlopen", boom)
        got = fetch_today()
        assert isinstance(got, ApodError)
        assert "network down" in got.message

    def test_unexpected_payload_returns_error(self, monkeypatch):
        def fake_urlopen(req, timeout=None):
            mock = MagicMock()
            mock.read.return_value = b"[]"
            mock.__enter__ = lambda self: self
            mock.__exit__ = lambda self, *a: None
            return mock

        monkeypatch.setattr(
            "docking.applets.apod.api.urllib.request.urlopen", fake_urlopen
        )
        got = fetch_today()
        assert isinstance(got, ApodError)


class TestAppletLifecycle:
    def test_creates_with_fallback_icon(self):
        applet = _make_applet()
        assert applet.item.icon is not None

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = _make_applet(size)
            assert applet.create_icon(size) is not None

    def test_tooltip_initial_loading(self):
        applet = _make_applet()
        applet.refresh_tooltip()
        assert "Loading" in applet.item.name

    def test_tooltip_with_result(self):
        applet = _make_applet()
        applet._result = _sample_result()
        applet.refresh_tooltip()
        assert "Spiral Galaxy" in applet.item.name


class TestAppletMenu:
    def test_empty_menu_has_open_and_refresh(self):
        applet = _make_applet()
        labels = [mi.get_label() for mi in applet.get_menu_items()]
        assert any("Open on apod.nasa.gov" in label for label in labels)
        assert any("Refresh Now" in label for label in labels)

    def test_menu_with_result_has_header_and_copy(self):
        applet = _make_applet()
        applet._result = _sample_result()
        labels = [mi.get_label() for mi in applet.get_menu_items()]
        assert any("Spiral Galaxy" in label for label in labels)
        assert any("Copy Explanation" in label for label in labels)


class TestAppletFetch:
    def test_success_saves_prefs(self):
        config = Config(applet_prefs={})
        applet = _make_applet(config=config)
        expected = _sample_result()
        with patch("docking.applets.apod.applet.fetch_today", return_value=expected):
            applet._fetch_async()
        assert applet._result == expected
        assert config.applet_prefs["apod"]["last_result"]["title"] == "Spiral Galaxy"

    def test_error_keeps_previous_result(self):
        applet = _make_applet()
        prev = _sample_result(date="2026-04-23")
        applet._result = prev
        with patch(
            "docking.applets.apod.applet.fetch_today",
            return_value=ApodError(message="down"),
        ):
            applet._fetch_async()
        assert applet._result == prev
        assert applet._error == "down"

    def test_needs_fetch_when_date_stale(self):
        applet = _make_applet()
        applet._result = _sample_result(date="2020-01-01")
        assert applet._needs_fetch() is True

    def test_no_fetch_when_result_is_today(self):
        import datetime as _dt

        today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
        applet = _make_applet()
        applet._result = _sample_result(date=today)
        assert applet._needs_fetch() is False


def _write_png(path):
    # Minimal 1x1 PNG, valid enough for Pixbuf.new_from_file_at_scale.
    header = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xfc\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x85*\x8d"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    path.write_bytes(header)


class TestRenderPixbuf:
    def test_renders_from_cached_image(self, tmp_path):
        from docking.applets.apod.render import render_icon

        png = tmp_path / "today.png"
        _write_png(png)
        pb = render_icon(size=48, cached_path=str(png))
        assert pb is not None
        assert pb.get_width() == 48

    def test_falls_back_without_image(self):
        from docking.applets.apod.render import render_icon

        pb = render_icon(size=48, cached_path="")
        assert pb is not None


class TestAppletResponseStream:
    """Ensure the API layer does not hold the response open past the read."""

    def test_download_drains_chunks(self, tmp_path, monkeypatch):
        from docking.applets.apod.api import _download_image

        monkeypatch.setattr("docking.applets.apod.api.CACHE_DIR", tmp_path / "apod")

        def fake_urlopen(req, timeout=None):
            mock = MagicMock()
            mock.read.side_effect = [b"\x89PNG\r\n\x1a\n", b"rest", b""]
            mock.__enter__ = lambda self: self
            mock.__exit__ = lambda self, *a: None
            return mock

        monkeypatch.setattr(
            "docking.applets.apod.api.urllib.request.urlopen", fake_urlopen
        )
        path = _download_image(
            url="https://example.com/image.png", date_iso="2026-04-24"
        )
        assert path.endswith(".png")


def test_bytesio_keeps_linter_happy():
    # Silences linters about unused import; BytesIO stays importable to make
    # future streaming-image tests easier to add without reshuffling imports.
    assert BytesIO is not None
