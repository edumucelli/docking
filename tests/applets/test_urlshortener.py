"""Tests for the URL Shortener applet."""

from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock, patch

from docking.applets.urlshortener import UrlShortenerApplet
from docking.applets.urlshortener.state import prefs_payload, shorten_url
from docking.core.config import Config


def _make_applet(config: Config | None = None) -> UrlShortenerApplet:
    return UrlShortenerApplet(48, config=config)


# -- State tests ---------------------------------------------------------------


class TestShortenUrl:
    def test_success(self):
        with patch("docking.applets.urlshortener.state.urllib.request.urlopen") as mock:
            mock.return_value.__enter__ = lambda s: s
            mock.return_value.__exit__ = MagicMock(return_value=False)
            mock.return_value.read.return_value = b"https://is.gd/abc123"
            result = shorten_url("https://example.com/very/long/url")
        assert result == "https://is.gd/abc123"

    def test_prepends_https_when_missing(self):
        with patch("docking.applets.urlshortener.state.urllib.request.urlopen") as mock:
            mock.return_value.__enter__ = lambda s: s
            mock.return_value.__exit__ = MagicMock(return_value=False)
            mock.return_value.read.return_value = b"https://is.gd/xyz"
            shorten_url("example.com")
        req = mock.call_args[0][0]
        assert "https%3A%2F%2Fexample.com" in req.full_url

    def test_empty_string(self):
        assert shorten_url("") == ""

    def test_whitespace_only(self):
        assert shorten_url("   ") == ""

    def test_http_error(self):
        with patch("docking.applets.urlshortener.state.urllib.request.urlopen") as mock:
            exc = urllib.error.HTTPError(
                url="", code=400, msg="Bad Request", hdrs=None, fp=None
            )
            exc.read = lambda: b"Invalid URL"
            mock.side_effect = exc
            result = shorten_url("not-a-url")
        assert result.startswith("Error")

    def test_network_error(self):
        with patch("docking.applets.urlshortener.state.urllib.request.urlopen") as mock:
            mock.side_effect = urllib.error.URLError("No network")
            result = shorten_url("https://example.com")
        assert "network" in result.lower()

    def test_timeout(self):
        with patch("docking.applets.urlshortener.state.urllib.request.urlopen") as mock:
            mock.side_effect = TimeoutError()
            result = shorten_url("https://example.com")
        assert "timed out" in result.lower()


class TestPrefsPayload:
    def test_round_trip(self):
        payload = prefs_payload(last_url="https://example.com")
        assert payload["last_url"] == "https://example.com"


# -- Applet tests --------------------------------------------------------------


class TestAppletCreation:
    def test_creates_with_icon(self):
        applet = _make_applet()
        assert applet.item.icon is not None

    def test_tooltip(self):
        applet = _make_applet()
        applet.refresh_tooltip()
        assert "URL" in applet.item.name

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = UrlShortenerApplet(size)
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None


class TestAppletPrefs:
    def test_loads_prefs(self):
        config = Config(
            applet_prefs={"urlshortener": {"last_url": "https://example.com"}}
        )
        applet = _make_applet(config=config)
        assert applet._last_url == "https://example.com"

    def test_defaults_without_config(self):
        applet = _make_applet()
        assert applet._last_url == ""

    def test_saves_prefs(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        applet = _make_applet(config=config)
        applet._last_url = "https://is.gd/test"
        applet._save_prefs()
        reloaded = Config.load(path)
        assert reloaded.applet_prefs["urlshortener"]["last_url"] == "https://is.gd/test"


class TestAppletDialog:
    def test_on_clicked_shows_dialog(self, monkeypatch):
        applet = _make_applet()
        show = MagicMock()
        monkeypatch.setattr(applet, "_show_dialog", show)
        applet.on_clicked()
        show.assert_called_once()

    def test_on_clicked_hides_visible_dialog(self):
        applet = _make_applet()
        dialog = MagicMock()
        dialog.get_visible.return_value = True
        applet._dialog = dialog
        applet.on_clicked()
        dialog.hide.assert_called_once()

    def test_stop_destroys_dialog(self):
        applet = _make_applet()
        dialog = MagicMock()
        applet._dialog = dialog
        applet.stop()
        dialog.destroy.assert_called_once()
        assert applet._dialog is None

    def test_stop_without_dialog(self):
        applet = _make_applet()
        applet.stop()  # should not raise
