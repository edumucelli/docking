"""Tests for the Quote applet."""

from unittest.mock import patch

from docking.applets.quote import (
    DEFAULT_SOURCE,
    SOURCE_LABELS,
    QuoteApplet,
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
    @patch("docking.applets.quote._http_get_json")
    def test_fetches_quotations_source(self, mock_get):
        mock_get.return_value = [
            {"q": "Alpha quote", "a": "Alice"},
            {"q": "Beta quote", "a": "Bob"},
        ]
        quotes = fetch_quotes(source="quotationspage", limit=5)
        assert len(quotes) == 2
        assert quotes[0] == QuoteEntry(text="Alpha quote", author="Alice")

    @patch("docking.applets.quote._http_get_json")
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

    @patch("docking.applets.quote._http_get_json")
    def test_fetches_chuck_source(self, mock_get):
        mock_get.return_value = {"value": "Chuck quote"}
        quotes = fetch_quotes(source="chucknorrisfactsfr", limit=1)
        assert quotes == [QuoteEntry(text="Chuck quote")]

    @patch("docking.applets.quote._http_get_json", side_effect=RuntimeError("boom"))
    def test_fetch_failure_returns_empty(self, _mock_get):
        assert fetch_quotes(source="quotationspage") == []


class TestQuoteApplet:
    def test_creates_with_icon(self):
        applet = QuoteApplet(48)
        assert applet.item.icon is not None
        assert applet._source == DEFAULT_SOURCE

    def test_click_advances_quotes(self):
        applet = QuoteApplet(48)
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
        applet = QuoteApplet(48)
        applet._quotes = [QuoteEntry(text="only")]
        applet._index = 0
        applet._current = QuoteEntry(text="only")

        with patch.object(applet, "_fetch_async") as fetch_mock:
            applet.on_clicked()
        fetch_mock.assert_called_once_with(show_first=True)

    def test_menu_contains_core_actions(self):
        applet = QuoteApplet(48)
        labels = [item.get_label() for item in applet.get_menu_items()]
        assert "Next Quote" in labels
        assert "Copy Quote" in labels
        assert "Refresh from Web" in labels
        assert "Source" in labels

    def test_menu_contains_legacy_source_labels(self):
        applet = QuoteApplet(48)
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
