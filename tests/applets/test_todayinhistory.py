"""Tests for the Today in History applet."""

from __future__ import annotations

from unittest.mock import patch

import pytest

today_public = pytest.importorskip(
    "docking.applets.todayinhistory",
    reason="Today in History applet is not available in this checkout",
)
today_applet_mod = pytest.importorskip(
    "docking.applets.todayinhistory.applet",
    reason="Today in History applet wiring is not available in this checkout",
)

TodayInHistoryApplet = today_public.TodayInHistoryApplet
HistoryEvent = today_public.HistoryEvent
fallback_today_in_history = today_public.fallback_today_in_history
fetch_today_in_history = today_public.fetch_today_in_history
format_history_event = today_public.format_history_event

ENTRY = HistoryEvent(
    year=1969,
    title="Apollo 11 lands on the Moon",
    summary="Neil Armstrong and Buzz Aldrin land in the Sea of Tranquility.",
    article_title="Apollo 11",
    article_url="https://en.wikipedia.org/wiki/Apollo_11",
)


class TestHistoryHelpers:
    def test_format_history_event_includes_year_and_content(self):
        text = format_history_event(ENTRY)

        assert "1969" in text
        assert "Apollo 11" in text
        assert "Sea of Tranquility" in text

    @patch("docking.applets.todayinhistory._http_get_json")
    def test_fetch_today_in_history_parses_wikipedia_response(self, mock_get):
        mock_get.return_value = {
            "events": [
                {
                    "year": 1969,
                    "text": "Apollo 11 lands on the Moon.",
                    "pages": [
                        {
                            "titles": {"normalized": "Apollo 11"},
                            "content_urls": {
                                "desktop": {
                                    "page": "https://en.wikipedia.org/wiki/Apollo_11"
                                }
                            },
                        }
                    ],
                }
            ]
        }

        entries = fetch_today_in_history(month=7, day=20)

        assert len(entries) == 1
        assert entries[0].year == 1969
        assert entries[0].title == "Apollo 11"
        assert "Moon" in entries[0].summary
        assert entries[0].article_url.endswith("/Apollo_11")

    @patch("docking.applets.todayinhistory._http_get_json", side_effect=RuntimeError)
    def test_fetch_failure_returns_empty(self, _mock_get):
        assert fetch_today_in_history(month=7, day=20) == []

    def test_fallback_returns_entries_for_any_date(self):
        entries = fallback_today_in_history(month=3, day=14)

        assert entries
        assert all(isinstance(entry, HistoryEvent) for entry in entries)


class TestTodayInHistoryApplet:
    def test_creates_with_icon_and_initial_event(self):
        applet = TodayInHistoryApplet(48)

        assert applet.item.icon is not None
        assert applet._current is not None

    def test_click_advances_and_wraps_events(self):
        applet = TodayInHistoryApplet(48)
        second = HistoryEvent(
            year=1989,
            title="World Wide Web proposal",
            summary="Tim Berners-Lee proposes an information management system.",
            article_title="World Wide Web",
            article_url="https://en.wikipedia.org/wiki/World_Wide_Web",
        )
        applet._events = [ENTRY, second]
        applet._index = -1
        applet._current = None

        applet.on_clicked()
        assert applet._current == ENTRY

        applet.on_clicked()
        assert applet._current == second

        applet.on_clicked()
        assert applet._current == ENTRY

    def test_menu_contains_core_actions(self):
        applet = TodayInHistoryApplet(48)
        applet._current = ENTRY

        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]

        assert "Wikipedia" in labels
        assert "Next Event" in labels
        assert "Refresh from Web" in labels
        assert "Open Article" in labels

    def test_menu_omits_open_article_without_url(self):
        applet = TodayInHistoryApplet(48)
        applet._current = HistoryEvent(
            year=44,
            title="Julius Caesar assassinated",
            summary="The Roman dictator is assassinated on the Ides of March.",
            article_title="",
            article_url="",
        )

        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]

        assert "Open Article" not in labels

    def test_on_fetch_result_uses_fallback_when_api_returns_empty(self, monkeypatch):
        applet = TodayInHistoryApplet(48)
        applet._current = None
        applet._loading = True
        fallback_entries = [ENTRY]
        monkeypatch.setattr(
            today_applet_mod,
            "fallback_today_in_history",
            lambda month, day: fallback_entries,
        )

        assert (
            applet._on_fetch_result(month=7, day=20, entries=[], show_first=True)
            is False
        )
        assert applet._current == ENTRY

    def test_refresh_tooltip_loading_state(self):
        applet = TodayInHistoryApplet(48)
        applet._loading = True
        applet._current = None

        applet.refresh_tooltip()

        assert "loading" in applet.item.name.lower()
