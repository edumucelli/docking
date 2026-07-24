"""Tests for the Quote applet."""

from unittest.mock import MagicMock, patch

import docking.applets.quote.applet as quote_mod
from docking.applets.quote.applet import QuoteApplet
from docking.applets.quote.state import (
    DEFAULT_SOURCE,
    SOURCE_LABELS,
    QuoteEntry,
    fetch_quotes,
    format_quote,
)
from docking.core.config import Config


class TestFormatQuote:
    def test_includes_author_when_present(self):
        entry = QuoteEntry(text="Stay hungry, stay foolish.", author="Steve Jobs")
        assert format_quote(entry) == '"Stay hungry, stay foolish." - Steve Jobs'

    def test_text_only_when_author_missing(self):
        entry = QuoteEntry(text="No author quote")
        assert format_quote(entry) == "No author quote"


class TestFetchQuotes:
    @patch("docking.applets.quote.state.http_get_json")
    def test_fetches_quotations_source(self, mock_get):
        mock_get.return_value = [
            {"q": "Alpha quote", "a": "Alice"},
            {"q": "Beta quote", "a": "Bob"},
        ]
        quotes = fetch_quotes(source="quotationspage", limit=5)
        assert len(quotes) == 2
        assert quotes[0] == QuoteEntry(text="Alpha quote", author="Alice")

    @patch("docking.applets.quote.state.http_get_json")
    def test_fetches_joke_sources(self, mock_get):
        mock_get.return_value = {
            "jokes": [
                {"type": "single", "joke": "First joke"},
                {"type": "single", "joke": "Second joke"},
            ]
        }
        quotes = fetch_quotes(source="qdb", limit=10)
        assert len(quotes) == 2
        assert quotes[0] == QuoteEntry(text="First joke")

    @patch("docking.applets.quote.state.http_get_json")
    def test_fetches_chuck_source(self, mock_get):
        mock_get.return_value = {"value": "Chuck quote"}
        quotes = fetch_quotes(source="chucknorrisfactsfr", limit=1)
        assert quotes == [QuoteEntry(text="Chuck quote")]

    @patch(
        "docking.applets.quote.state.http_get_json", side_effect=RuntimeError("boom")
    )
    def test_fetch_failure_returns_empty(self, _mock_get):
        assert fetch_quotes(source="quotationspage") == []


class TestQuoteApplet:
    def test_creates_with_icon(self):
        applet = QuoteApplet(48, config=Config())
        assert applet.item.icon is not None
        assert applet._source == DEFAULT_SOURCE

    def test_click_advances_quotes(self):
        applet = QuoteApplet(48, config=Config())
        applet._quotes = [
            QuoteEntry(text="one"),
            QuoteEntry(text="two"),
        ]
        applet._index = -1
        applet._current = None

        applet.on_clicked()
        assert applet._current == QuoteEntry(text="one")

        applet.on_clicked()
        assert applet._current == QuoteEntry(text="two")

    def test_exhausted_click_triggers_fetch(self):
        applet = QuoteApplet(48, config=Config())
        applet._quotes = [QuoteEntry(text="only")]
        applet._index = 0
        applet._current = QuoteEntry(text="only")

        with patch.object(applet, "_fetch_async") as fetch_mock:
            applet.on_clicked()
        fetch_mock.assert_called_once_with(show_first=True)

    def test_menu_contains_core_actions(self):
        applet = QuoteApplet(48, config=Config())
        labels = [item.get_label() for item in applet.get_menu_items()]
        assert "Next Quote" in labels
        assert "Copy Quote" in labels
        assert "Refresh Now" in labels
        assert "Source" in labels

    def test_menu_contains_legacy_source_labels(self):
        applet = QuoteApplet(48, config=Config())
        labels = [item.get_label() for item in applet.get_menu_items()]
        for label in SOURCE_LABELS.values():
            assert label in labels

    def test_set_source_saves_preference(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        applet = QuoteApplet(48, config=config)

        applet._set_source("qdb")

        reloaded = Config.load(path)
        assert reloaded.applet_prefs["quote"]["source"] == "qdb"
        assert applet._source == "qdb"


class TestQuoteAppletBranches:
    def test_on_source_toggled_ignores_inactive_widget(self):
        applet = QuoteApplet(48, config=Config())
        applet._set_source = MagicMock()
        widget = MagicMock()
        widget.get_active.return_value = False
        applet._on_source_toggled(widget, "qdb")
        applet._set_source.assert_not_called()

    def test_set_source_ignores_invalid_and_same_source(self):
        applet = QuoteApplet(48, config=Config())
        applet._fetch_async = MagicMock()
        applet._set_source("invalid-source")
        applet._set_source(applet._source)
        applet._fetch_async.assert_not_called()

    def test_refresh_from_web_delegates_to_async_fetch(self):
        applet = QuoteApplet(48, config=Config())
        applet._fetch_async = MagicMock()
        applet._refresh_from_web()
        applet._fetch_async.assert_called_once_with(show_first=True)

    def test_copy_current_quote_uses_clipboard(self, monkeypatch):
        applet = QuoteApplet(48, config=Config())
        applet._current = QuoteEntry(text="hello", author="world")
        clipboard = MagicMock()
        monkeypatch.setattr(quote_mod.Gtk.Clipboard, "get", lambda *_a, **_k: clipboard)

        applet._copy_current_quote()

        clipboard.set_text.assert_called_once()
        clipboard.store.assert_called_once()

    def test_copy_current_quote_handles_clipboard_errors(self):
        applet = QuoteApplet(48, config=Config())
        applet._current = QuoteEntry(text="x")
        applet._clipboard = MagicMock()
        applet._clipboard.set_text.side_effect = RuntimeError("boom")

        applet._copy_current_quote()

    def test_fetch_async_noop_when_loading(self):
        applet = QuoteApplet(48, config=Config())
        applet._loading = True
        applet.present = MagicMock()
        applet._fetch_async(show_first=True)
        applet.present.assert_not_called()

    def test_fetch_async_runs_worker_and_posts_idle_result(self, monkeypatch):
        applet = QuoteApplet(48, config=Config())
        monkeypatch.setattr(
            quote_mod, "fetch_quotes", lambda source, limit: [QuoteEntry(text="q")]
        )
        calls = []
        monkeypatch.setattr(
            quote_mod.GLib,
            "idle_add",
            lambda cb, source, quotes, show_first: calls.append(
                (cb, source, quotes, show_first)
            ),
        )

        class FakeThread:
            def __init__(self, target, daemon):
                self._target = target
                self.daemon = daemon

            def start(self):
                self._target()

        monkeypatch.setattr(quote_mod.threading, "Thread", FakeThread)
        applet._fetch_async(show_first=True)
        assert calls
        assert calls[0][0] == applet._on_fetch_result
        assert calls[0][1] == applet._source
        assert calls[0][3] is True

    def test_on_fetch_result_ignores_stale_source(self):
        applet = QuoteApplet(48, config=Config())
        applet._source = "qdb"
        applet._loading = True
        assert (
            applet._on_fetch_result("other", [QuoteEntry(text="x")], show_first=True)
            is False
        )

    def test_on_fetch_result_applies_quotes_and_fallback(self, monkeypatch):
        applet = QuoteApplet(48, config=Config())
        applet._source = "qdb"
        applet._loading = True
        applet.present = MagicMock()

        quotes = [QuoteEntry(text="one"), QuoteEntry(text="two")]
        assert applet._on_fetch_result("qdb", quotes, show_first=True) is False
        assert applet._current == QuoteEntry(text="one")

        applet._current = None
        monkeypatch.setattr(
            quote_mod, "source_fallback", lambda source: [QuoteEntry(text="fallback")]
        )
        assert applet._on_fetch_result("qdb", [], show_first=False) is False
        assert applet._current == QuoteEntry(text="fallback")

    def test_refresh_tooltip_loading_state(self):
        applet = QuoteApplet(48, config=Config())
        applet._loading = True
        applet._current = None
        applet.refresh_tooltip()
        assert "loading" in applet.item.name.lower()
