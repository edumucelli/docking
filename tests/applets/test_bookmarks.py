"""Tests for the bookmarks applet."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import docking.applets.bookmarks.applet as bookmarks_applet_mod
from docking.applets.bookmarks.state import (
    Bookmark,
    bookmarks_from_prefs,
    prefs_from_bookmarks,
    tooltip_text,
    truncate_label,
)


class TestState:
    """Pure-logic state tests."""

    def test_round_trip_serialization(self):
        """Given bookmarks, when serialized then deserialized, then equal."""
        bookmarks = [
            Bookmark(name="Example", url="https://example.com"),
            Bookmark(name="Docs", url="https://docs.python.org"),
        ]
        prefs = prefs_from_bookmarks(bookmarks=bookmarks)
        restored = bookmarks_from_prefs(prefs=prefs)
        assert restored == bookmarks

    def test_empty_prefs_returns_empty(self):
        """Given None prefs, when loading, then empty list."""
        assert bookmarks_from_prefs(prefs=None) == []
        assert bookmarks_from_prefs(prefs={}) == []

    def test_invalid_entries_skipped(self):
        """Given malformed entries, when loading, then skipped."""
        prefs = {"bookmarks": [{"name": "ok", "url": "u"}, "bad", {"x": 1}]}
        result = bookmarks_from_prefs(prefs=prefs)
        assert len(result) == 1
        assert result[0].name == "ok"

    def test_truncate_label_short(self):
        """Given short text, when truncated, then unchanged."""
        assert truncate_label("Hello") == "Hello"

    def test_truncate_label_long(self):
        """Given long text, when truncated, then ends with ellipsis."""
        long_text = "A" * 50
        result = truncate_label(long_text)
        assert len(result) == 30
        assert result.endswith("\u2026")

    def test_tooltip_text_empty(self):
        """Given no bookmarks, when tooltip, then 'No bookmarks'."""
        assert tooltip_text(bookmarks=[]) == "No bookmarks"

    def test_tooltip_text_with_count(self):
        """Given bookmarks, when tooltip, then shows count."""
        bms = [Bookmark(name="A", url="a"), Bookmark(name="B", url="b")]
        assert tooltip_text(bookmarks=bms) == "2 bookmarks"


class TestRender:
    """Icon rendering tests."""

    def test_render_icon_returns_pixbuf(self):
        """Given valid size, when rendering, then returns pixbuf."""
        from docking.applets.bookmarks.render import render_icon

        for size in (32, 48, 64):
            pixbuf = render_icon(size=size, count=0)
            assert pixbuf is not None

    def test_render_icon_with_count(self):
        """Given count > 0, when rendering, then returns pixbuf."""
        from docking.applets.bookmarks.render import render_icon

        for count in (1, 5):
            pixbuf = render_icon(size=48, count=count)
            assert pixbuf is not None


class TestApplet:
    """Applet lifecycle tests."""

    def test_creates_with_empty_bookmarks(self):
        """Given no config, when created, then empty bookmarks."""
        from docking.applets.bookmarks import BookmarksApplet

        applet = BookmarksApplet(icon_size=48)
        assert applet.item.icon is not None
        assert "No bookmarks" in applet.item.name

    def test_loads_from_prefs(self):
        """Given config with bookmarks, when created, then loaded."""
        from docking.applets.bookmarks import BookmarksApplet
        from docking.core.config import Config

        config = Config()
        config.applet_prefs["bookmarks"] = {
            "bookmarks": [{"name": "Test", "url": "https://test.com"}],
        }
        applet = BookmarksApplet(icon_size=48, config=config)
        assert len(applet._bookmarks) == 1
        assert "1 bookmarks" in applet.item.name

    def test_on_clicked_opens_first_url(self):
        """Given bookmarks, when clicked, then opens first URL."""
        from docking.applets.bookmarks import BookmarksApplet

        applet = BookmarksApplet(icon_size=48)
        applet._bookmarks = [Bookmark(name="Ex", url="https://example.com")]

        with patch("docking.applets.bookmarks.applet.Gio") as mock_gio:
            applet.on_clicked()
            mock_gio.AppInfo.launch_default_for_uri.assert_called_once_with(
                "https://example.com", None
            )

    def test_on_clicked_noop_when_empty(self):
        """Given no bookmarks, when clicked, then no-op."""
        from docking.applets.bookmarks import BookmarksApplet

        applet = BookmarksApplet(icon_size=48)
        with patch("docking.applets.bookmarks.applet.Gio") as mock_gio:
            applet.on_clicked()
            mock_gio.AppInfo.launch_default_for_uri.assert_not_called()

    def test_menu_items_open_remove_and_add_callbacks(self, monkeypatch):
        from docking.applets.bookmarks import BookmarksApplet

        class _FakeMenuItem:
            def __init__(self, label: str = "") -> None:
                self._signals: dict[str, object] = {}

            def connect(self, signal: str, callback) -> None:
                self._signals[signal] = callback

            def emit(self, signal: str) -> None:
                self._signals[signal](self)

            def set_sensitive(self, _value: bool) -> None:
                return

        monkeypatch.setattr(bookmarks_applet_mod.Gtk, "MenuItem", _FakeMenuItem)
        monkeypatch.setattr(
            bookmarks_applet_mod.Gtk, "SeparatorMenuItem", _FakeMenuItem
        )

        applet = BookmarksApplet(icon_size=48)
        applet._bookmarks = [Bookmark(name="Ex", url="https://example.com")]
        open_url = []
        add_calls = []
        remove_calls = []
        monkeypatch.setattr(applet, "_open_url", lambda url: open_url.append(url))
        monkeypatch.setattr(applet, "_show_add_dialog", lambda: add_calls.append(True))
        monkeypatch.setattr(applet, "_remove_all", lambda: remove_calls.append(True))

        items = applet.get_menu_items()

        items[0].emit("activate")
        items[2].emit("activate")
        items[3].emit("activate")

        assert open_url == ["https://example.com"]
        assert add_calls == [True]
        assert remove_calls == [True]

    def test_open_url_logs_glib_error(self, monkeypatch):
        from docking.applets.bookmarks import BookmarksApplet

        applet = BookmarksApplet(icon_size=48)
        logger = SimpleNamespace(warning=lambda *_args, **_kwargs: None)

        def bind(**_kwargs):
            return logger

        monkeypatch.setattr(bookmarks_applet_mod.GLib, "Error", RuntimeError)
        monkeypatch.setattr(
            bookmarks_applet_mod.Gio.AppInfo,
            "launch_default_for_uri",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(bookmarks_applet_mod._log, "bind", bind)

        applet._open_url("https://example.com")

    def test_show_add_dialog_persists_valid_bookmark(self, monkeypatch):
        from docking.applets.bookmarks import BookmarksApplet

        class _FakeBox:
            def __init__(self):
                self.children = []

            def set_spacing(self, _value):
                return

            def set_margin_start(self, _value):
                return

            def set_margin_end(self, _value):
                return

            def set_margin_top(self, _value):
                return

            def set_margin_bottom(self, _value):
                return

            def pack_start(self, child, *_args):
                self.children.append(child)

        class _FakeDialog:
            def __init__(self, **_kwargs):
                self.content = _FakeBox()
                self.destroyed = False

            def add_buttons(self, *_args):
                return

            def set_default_size(self, *_args):
                return

            def set_position(self, *_args):
                return

            def get_content_area(self):
                return self.content

            def show_all(self):
                return

            def run(self):
                return bookmarks_applet_mod.Gtk.ResponseType.OK

            def destroy(self):
                self.destroyed = True

        class _FakeEntry:
            def __init__(self, text):
                self._text = text

            def set_placeholder_text(self, _text):
                return

            def get_text(self):
                return self._text

        entries = iter([_FakeEntry("Docs"), _FakeEntry("https://docs.python.org")])
        monkeypatch.setattr(
            bookmarks_applet_mod.Gtk, "Dialog", lambda **kwargs: _FakeDialog(**kwargs)
        )
        monkeypatch.setattr(bookmarks_applet_mod.Gtk, "Entry", lambda: next(entries))

        applet = BookmarksApplet(icon_size=48)
        applet.save_prefs = lambda prefs: saved.append(prefs)  # type: ignore[method-assign]
        applet.present = lambda: presented.append(True)  # type: ignore[method-assign]
        saved: list[dict[str, object]] = []
        presented: list[bool] = []

        applet._show_add_dialog()

        assert applet._bookmarks == [
            Bookmark(name="Docs", url="https://docs.python.org")
        ]
        assert saved
        assert presented == [True]

    def test_show_add_dialog_ignores_blank_values(self, monkeypatch):
        from docking.applets.bookmarks import BookmarksApplet

        class _FakeBox:
            def set_spacing(self, _value):
                return

            def set_margin_start(self, _value):
                return

            def set_margin_end(self, _value):
                return

            def set_margin_top(self, _value):
                return

            def set_margin_bottom(self, _value):
                return

            def pack_start(self, *_args):
                return

        class _FakeDialog:
            def __init__(self, **_kwargs):
                self.content = _FakeBox()

            def add_buttons(self, *_args):
                return

            def set_default_size(self, *_args):
                return

            def set_position(self, *_args):
                return

            def get_content_area(self):
                return self.content

            def show_all(self):
                return

            def run(self):
                return bookmarks_applet_mod.Gtk.ResponseType.OK

            def destroy(self):
                return

        class _FakeEntry:
            def __init__(self, text):
                self._text = text

            def set_placeholder_text(self, _text):
                return

            def get_text(self):
                return self._text

        entries = iter([_FakeEntry(""), _FakeEntry("   ")])
        monkeypatch.setattr(
            bookmarks_applet_mod.Gtk, "Dialog", lambda **kwargs: _FakeDialog(**kwargs)
        )
        monkeypatch.setattr(bookmarks_applet_mod.Gtk, "Entry", lambda: next(entries))

        applet = BookmarksApplet(icon_size=48)
        applet.save_prefs = lambda prefs: (_ for _ in ()).throw(AssertionError(prefs))  # type: ignore[method-assign]

        applet._show_add_dialog()

        assert applet._bookmarks == []

    def test_remove_all_clears_state_and_presents(self):
        from docking.applets.bookmarks import BookmarksApplet

        applet = BookmarksApplet(icon_size=48)
        applet._bookmarks = [Bookmark(name="Ex", url="https://example.com")]
        saved: list[dict[str, object]] = []
        applet.save_prefs = lambda prefs: saved.append(prefs)  # type: ignore[method-assign]
        presented: list[bool] = []
        applet.present = lambda: presented.append(True)  # type: ignore[method-assign]

        applet._remove_all()

        assert applet._bookmarks == []
        assert saved == [{"bookmarks": []}]
        assert presented == [True]
