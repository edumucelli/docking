"""Tests for the bookmarks applet."""

from __future__ import annotations

from unittest.mock import patch

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
