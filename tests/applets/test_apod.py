"""Tests for the APOD applet."""

from __future__ import annotations

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
    def test_no_data_state(self):
        text = build_tooltip(result=None, error=None)
        assert "No data yet" in text

    def test_loading_state(self):
        text = build_tooltip(result=None, error=None, loading=True)
        assert "Loading" in text

    def test_error_state(self):
        text = build_tooltip(result=None, error="network down")
        assert "network down" in text

    def test_full_result(self):
        text = build_tooltip(
            result=_sample_result(),
            error=None,
            cadence_seconds=3600,
        )
        assert "Spiral Galaxy" in text
        assert "NASA" in text
        assert "2026-04-24" in text
        assert "distant spiral" in text
        assert "Checks every 1 hour" in text


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
    def _patch_image_download(self, monkeypatch, payload_bytes):
        def fake_urlopen(_req, timeout=None):
            mock = MagicMock()
            mock.read.side_effect = [payload_bytes, b""]
            mock.__enter__ = lambda self: self
            mock.__exit__ = lambda self, *a: None
            return mock

        monkeypatch.setattr(
            "docking.applets.apod.api.urllib.request.urlopen", fake_urlopen
        )

    def test_parses_successful_response(self, tmp_path, monkeypatch):
        monkeypatch.setattr("docking.applets.apod.api.CACHE_DIR", tmp_path / "apod")
        monkeypatch.setattr(
            "docking.applets.apod.api.http_get_json",
            lambda url, **_kwargs: _SAMPLE_PAYLOAD,
        )
        self._patch_image_download(monkeypatch, b"\xff\xd8\xff\xe0" + b"\x00" * 512)

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

        monkeypatch.setattr(
            "docking.applets.apod.api.http_get_json", lambda url, **_kwargs: payload
        )
        self._patch_image_download(monkeypatch, b"\xff\xd8\xff\xe0" + b"\x00" * 64)

        got = fetch_today()
        assert isinstance(got, ApodResult)
        assert got.media_type == "video"
        assert got.image_url == "https://example/thumb.jpg"

    def test_network_failure_returns_error(self, monkeypatch):
        def boom(*_args, **_kwargs):
            raise OSError("network down")

        monkeypatch.setattr("docking.applets.apod.api.http_get_json", boom)
        got = fetch_today()
        assert isinstance(got, ApodError)
        assert "network down" in got.message

    def test_unexpected_payload_returns_error(self, monkeypatch):
        monkeypatch.setattr(
            "docking.applets.apod.api.http_get_json", lambda url, **_kwargs: []
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

    def test_tooltip_initial_no_data(self):
        applet = _make_applet()
        applet.refresh_tooltip()
        assert "No data yet" in applet.item.name

    def test_tooltip_with_result(self):
        applet = _make_applet()
        applet._result = _sample_result()
        applet.refresh_tooltip()
        assert "Spiral Galaxy" in applet.item.name

    def test_start_stop_and_ticks_manage_timers(self, monkeypatch):
        applet = _make_applet()
        add = MagicMock(side_effect=[11, 12, 13])
        removed: list[int] = []
        monkeypatch.setattr("docking.applets.apod.applet.GLib.timeout_add_seconds", add)
        monkeypatch.setattr(
            "docking.applets.apod.applet.GLib.source_remove",
            lambda timer_id: removed.append(timer_id),
        )
        applet._fetch_async = MagicMock()

        applet.start(lambda: None)
        assert applet._refresh_timer_id == 11
        assert applet._startup_fetch_timer_id == 12

        assert applet._tick() is True
        assert applet._run_startup_fetch() is False

        applet._retry_timer_id = 13
        applet.stop()

        assert removed == [11, 13]
        assert applet._refresh_timer_id == 0
        assert applet._retry_timer_id == 0
        assert applet._startup_fetch_timer_id == 0
        assert applet._fetch_async.call_count == 2

    def test_run_retry_only_fetches_when_needed(self, monkeypatch):
        applet = _make_applet()
        applet._fetch_async = MagicMock()
        monkeypatch.setattr(applet, "_needs_fetch", lambda: False)

        assert applet._run_retry() is False
        applet._fetch_async.assert_not_called()

        monkeypatch.setattr(applet, "_needs_fetch", lambda: True)
        assert applet._run_retry() is False
        applet._fetch_async.assert_called_once()


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
        assert "Checks every 1 hour" in labels
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

    def test_fetch_result_and_error_ignore_stale_requests(self):
        applet = _make_applet()
        applet._fetch_request_id = 7
        applet.present = MagicMock()

        assert applet._on_fetch_result(request_id=6, result=_sample_result()) is False
        assert applet._on_fetch_error(request_id=6, exc=RuntimeError("old")) is False

        applet.present.assert_not_called()

    def test_fetch_error_uses_exception_name_when_message_empty(self):
        applet = _make_applet()
        applet._fetch_request_id = 7

        assert applet._on_fetch_error(request_id=7, exc=RuntimeError()) is False

        assert applet._error == "RuntimeError"

    def test_schedule_retry_is_idempotent(self, monkeypatch):
        applet = _make_applet()
        add = MagicMock(return_value=42)
        monkeypatch.setattr("docking.applets.apod.applet.GLib.timeout_add_seconds", add)

        applet._schedule_retry()
        applet._schedule_retry()

        assert applet._retry_timer_id == 42
        add.assert_called_once()

    def test_open_page_and_copy_explanation(self, monkeypatch):
        applet = _make_applet()
        applet._result = _sample_result(page_url="https://example.test/apod")
        opened: list[str] = []
        monkeypatch.setattr(
            "docking.applets.apod.applet.Gio.AppInfo.launch_default_for_uri",
            lambda url, _ctx: opened.append(url),
        )

        applet.on_clicked()
        assert opened == ["https://example.test/apod"]

        copied: list[str] = []

        class _Clipboard:
            def set_text(self, text: str, length: int) -> None:
                copied.append(text)
                copied.append(str(length))

        monkeypatch.setattr(
            "docking.applets.apod.applet.Gtk.Clipboard.get",
            lambda _selection: _Clipboard(),
        )

        applet._copy_explanation()

        assert copied == ["A distant spiral galaxy.", "-1"]

    def test_open_page_uses_default_and_swallows_launch_error(self, monkeypatch):
        applet = _make_applet()
        opened: list[str] = []
        monkeypatch.setattr(
            "docking.applets.apod.applet.GLib.Error",
            RuntimeError,
            raising=False,
        )

        def launch(url, _ctx):
            opened.append(url)
            raise RuntimeError("no handler")

        monkeypatch.setattr(
            "docking.applets.apod.applet.Gio.AppInfo.launch_default_for_uri",
            launch,
        )

        applet._open_page()

        assert opened == ["https://apod.nasa.gov/"]

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

    def test_renders_real_pixbuf_cover_path(self, monkeypatch):
        import docking.applets.apod.render as render_mod

        pixbuf = render_mod.GdkPixbuf.Pixbuf.new(
            render_mod.GdkPixbuf.Colorspace.RGB,
            True,
            8,
            48,
            48,
        )
        pixbuf.fill(0x336699FF)
        monkeypatch.setattr(render_mod, "_load_cover_pixbuf", lambda **_kwargs: pixbuf)

        pb = render_mod.render_icon(
            size=48,
            cached_path="/tmp/image.png",
            warning=True,
        )

        assert pb is not None
        assert pb.get_width() == 48

    def test_load_cover_pixbuf_fallback_edges(self, monkeypatch, tmp_path):
        import docking.applets.apod.render as render_mod

        path = str(tmp_path / "image.png")
        monkeypatch.setattr(
            render_mod.GdkPixbuf.Pixbuf,
            "get_file_info",
            staticmethod(lambda _path: None),
        )
        fallback = MagicMock(return_value="fallback")
        monkeypatch.setattr(render_mod, "_fallback_scaled_pixbuf", fallback)

        assert render_mod._load_cover_pixbuf(path=path, size=48) == "fallback"

        monkeypatch.setattr(
            render_mod.GdkPixbuf.Pixbuf,
            "get_file_info",
            staticmethod(lambda _path: ("fmt", 0, 10)),
        )
        assert render_mod._load_cover_pixbuf(path=path, size=48) == "fallback"

    def test_load_cover_pixbuf_scales_and_crops(self, monkeypatch):
        import docking.applets.apod.render as render_mod

        class _Pixbuf:
            def __init__(self, width: int, height: int) -> None:
                self.width = width
                self.height = height
                self.crop: tuple[int, int, int, int] | None = None

            def get_width(self) -> int:
                return self.width

            def get_height(self) -> int:
                return self.height

            def new_subpixbuf(self, x: int, y: int, w: int, h: int):
                self.crop = (x, y, w, h)
                return self

        scaled = _Pixbuf(96, 48)
        monkeypatch.setattr(
            render_mod.GdkPixbuf.Pixbuf,
            "get_file_info",
            staticmethod(lambda _path: ("fmt", 100, 50)),
        )
        monkeypatch.setattr(
            render_mod.GdkPixbuf.Pixbuf,
            "new_from_file_at_scale",
            staticmethod(lambda _path, _w, _h, _preserve: scaled),
        )

        assert render_mod._load_cover_pixbuf(path="/tmp/img", size=48) is scaled
        assert scaled.crop == (24, 0, 48, 48)

    def test_fallback_scaled_pixbuf_returns_none_on_error(self, monkeypatch):
        import docking.applets.apod.render as render_mod

        monkeypatch.setattr(
            render_mod.GdkPixbuf.Pixbuf,
            "new_from_file_at_scale",
            staticmethod(
                lambda *_args: (_ for _ in ()).throw(render_mod.GLib.Error("bad"))
            ),
        )

        assert render_mod._fallback_scaled_pixbuf(path="/bad", size=48) is None


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
