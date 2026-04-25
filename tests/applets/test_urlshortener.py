"""Tests for the URL Shortener applet."""

from __future__ import annotations

import urllib.error
from unittest.mock import MagicMock, patch

import docking.applets.urlshortener.applet as urlshortener_applet_mod
from docking.applets.urlshortener.applet import UrlShortenerApplet
from docking.applets.urlshortener.state import prefs_payload, shorten_url
from docking.core.config import Config


def _make_applet(config: Config | None = None) -> UrlShortenerApplet:
    return UrlShortenerApplet(48, config=config)


class _FakeContentArea:
    def __init__(self) -> None:
        self.children: list[object] = []

    def set_spacing(self, _value: int) -> None:
        return

    def set_margin_start(self, _value: int) -> None:
        return

    def set_margin_end(self, _value: int) -> None:
        return

    def set_margin_top(self, _value: int) -> None:
        return

    def set_margin_bottom(self, _value: int) -> None:
        return

    def pack_start(self, child, *_args) -> None:
        self.children.append(child)

    def show_all(self) -> None:
        return


class _FakeDialog:
    def __init__(self, **_kwargs) -> None:
        self.visible = False
        self.destroyed = False
        self._content_area = _FakeContentArea()
        self._callbacks: dict[str, object] = {}

    def set_default_size(self, *_args) -> None:
        return

    def set_position(self, *_args) -> None:
        return

    def set_resizable(self, _value: bool) -> None:
        return

    def get_content_area(self) -> _FakeContentArea:
        return self._content_area

    def connect(self, signal: str, callback) -> None:
        self._callbacks[signal] = callback

    def show_all(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def get_visible(self) -> bool:
        return self.visible

    def destroy(self) -> None:
        self.destroyed = True
        self.visible = False

    def emit(self, signal: str, *args) -> None:
        callback = self._callbacks[signal]
        callback(self, *args)


class _FakeEntry:
    def __init__(self) -> None:
        self._text = ""
        self._callbacks: dict[str, object] = {}

    def set_placeholder_text(self, _text: str) -> None:
        return

    def set_text(self, text: str) -> None:
        self._text = text

    def get_text(self) -> str:
        return self._text

    def connect(self, signal: str, callback) -> None:
        self._callbacks[signal] = callback

    def emit(self, signal: str) -> None:
        callback = self._callbacks[signal]
        callback(self)

    def grab_focus(self) -> None:
        return


class _FakeButton:
    def __init__(self, label: str = "") -> None:
        self._label = label
        self._sensitive = True
        self._visible = True
        self._callbacks: dict[str, object] = {}

    def connect(self, signal: str, callback) -> None:
        self._callbacks[signal] = callback

    def emit(self, signal: str) -> None:
        callback = self._callbacks[signal]
        callback(self)

    def set_sensitive(self, value: bool) -> None:
        self._sensitive = value

    def get_sensitive(self) -> bool:
        return self._sensitive

    def set_visible(self, value: bool) -> None:
        self._visible = value

    def get_visible(self) -> bool:
        return self._visible

    def set_label(self, label: str) -> None:
        self._label = label

    def get_label(self) -> str:
        return self._label


class _FakeLabel:
    def __init__(self, label: str = "") -> None:
        self._text = label

    def set_text(self, text: str) -> None:
        self._text = text

    def get_text(self) -> str:
        return self._text

    def set_selectable(self, _value: bool) -> None:
        return

    def set_line_wrap(self, _value: bool) -> None:
        return

    def set_max_width_chars(self, _value: int) -> None:
        return


def _patch_dialog_widgets(monkeypatch) -> None:
    monkeypatch.setattr(urlshortener_applet_mod.Gtk, "Dialog", _FakeDialog)
    monkeypatch.setattr(urlshortener_applet_mod.Gtk, "Entry", _FakeEntry)
    monkeypatch.setattr(urlshortener_applet_mod.Gtk, "Button", _FakeButton)
    monkeypatch.setattr(urlshortener_applet_mod.Gtk, "Label", _FakeLabel)


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

    def test_show_dialog_builds_controls_and_cleans_up_on_response(self, monkeypatch):
        applet = _make_applet(
            config=Config(
                applet_prefs={"urlshortener": {"last_url": "https://example.com"}}
            )
        )
        _patch_dialog_widgets(monkeypatch)

        applet._show_dialog()

        assert applet._dialog is not None
        assert applet._url_entry is not None
        assert applet._url_entry.get_text() == "https://example.com"
        applet._dialog.emit("response", 0)
        assert applet._dialog is None

    def test_do_shorten_is_noop_without_widgets(self):
        applet = _make_applet()
        applet._url_entry = None
        applet._shorten_btn = None

        applet._do_shorten()

    def test_do_shorten_is_noop_for_blank_url(self, monkeypatch):
        applet = _make_applet()
        _patch_dialog_widgets(monkeypatch)
        applet._show_dialog()
        assert applet._url_entry is not None
        assert applet._shorten_btn is not None
        applet._url_entry.set_text("   ")

        applet._do_shorten()

        assert applet._shorten_btn.get_sensitive() is True
        applet.stop()

    def test_do_shorten_starts_worker_and_dispatches_result(self, monkeypatch):
        applet = _make_applet()
        _patch_dialog_widgets(monkeypatch)
        applet._show_dialog()
        applet._ensure_result_row()
        assert applet._url_entry is not None
        assert applet._shorten_btn is not None
        applet._url_entry.set_text("https://example.com")
        idle_add = MagicMock(side_effect=lambda fn, *args: fn(*args))
        monkeypatch.setattr(urlshortener_applet_mod.GLib, "idle_add", idle_add)
        monkeypatch.setattr(
            urlshortener_applet_mod, "shorten_url", lambda url: "https://is.gd/abc"
        )

        class _Thread:
            def __init__(self, *, target, daemon):
                self._target = target
                self.daemon = daemon

            def start(self):
                self._target()

        monkeypatch.setattr(urlshortener_applet_mod.threading, "Thread", _Thread)

        applet._do_shorten()

        assert any(
            call.args
            == (
                applet._on_result,
                "https://example.com",
                "https://is.gd/abc",
            )
            for call in idle_add.call_args_list
        )
        assert applet._last_result == "https://is.gd/abc"
        assert applet._shorten_btn.get_sensitive() is True
        applet.stop()

    def test_on_result_error_hides_copy_and_skips_save(self, monkeypatch):
        applet = _make_applet()
        _patch_dialog_widgets(monkeypatch)
        applet._show_dialog()
        save = MagicMock()
        applet.save_prefs = save

        applet._on_result("https://example.com", "Error: bad url")

        assert applet._result_label is not None
        assert applet._result_label.get_text() == "Error: bad url"
        assert applet._copy_btn is not None
        assert applet._copy_btn.get_visible() is False
        assert applet._last_result == ""
        save.assert_not_called()
        applet.stop()

    def test_on_result_success_persists_and_shows_copy(self, tmp_path, monkeypatch):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        applet = _make_applet(config=config)
        _patch_dialog_widgets(monkeypatch)
        applet._show_dialog()

        assert applet._on_result("https://example.com", "https://is.gd/ok") is False

        assert applet._result_label is not None
        assert applet._result_label.get_text() == "https://is.gd/ok"
        assert applet._copy_btn is not None
        assert applet._copy_btn.get_visible() is True
        assert applet._last_url == "https://example.com"
        reloaded = Config.load(path)
        assert (
            reloaded.applet_prefs["urlshortener"]["last_url"] == "https://example.com"
        )
        applet.stop()

    def test_ensure_result_row_requires_dialog(self):
        applet = _make_applet()
        applet._dialog = None
        applet._result_box = None

        applet._ensure_result_row()

        assert applet._result_box is None

    def test_do_copy_is_noop_without_last_result(self, monkeypatch):
        applet = _make_applet()
        applet._last_result = ""
        monkeypatch.setattr(
            urlshortener_applet_mod.Gtk.Clipboard,
            "get",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("should not be used")),
        )

        applet._do_copy()

    def test_do_copy_updates_clipboard_and_button_label(self, monkeypatch):
        applet = _make_applet()
        _patch_dialog_widgets(monkeypatch)
        applet._show_dialog()
        applet._ensure_result_row()
        applet._last_result = "https://is.gd/copied"
        clipboard = MagicMock()
        timeout_add = MagicMock()
        monkeypatch.setattr(
            urlshortener_applet_mod.Gtk.Clipboard, "get", lambda *_args: clipboard
        )
        monkeypatch.setattr(urlshortener_applet_mod.GLib, "timeout_add", timeout_add)

        applet._do_copy()

        clipboard.set_text.assert_called_once_with("https://is.gd/copied", -1)
        clipboard.store.assert_called_once_with()
        assert applet._copy_btn is not None
        assert applet._copy_btn.get_label() == "Copied!"
        timeout_add.assert_called_once()
        applet.stop()

    def test_reset_copy_label_restores_default(self):
        applet = _make_applet()
        applet._copy_btn = MagicMock()

        assert applet._reset_copy_label() is False
        applet._copy_btn.set_label.assert_called_once_with("Copy")
