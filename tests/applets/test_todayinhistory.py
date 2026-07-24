"""Tests for the Today in History applet."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from docking.core.config import Config

pytest.importorskip(
    "docking.applets.todayinhistory",
    reason="Today in History applet is not available in this checkout",
)
today_applet_mod = pytest.importorskip(
    "docking.applets.todayinhistory.applet",
    reason="Today in History applet wiring is not available in this checkout",
)
today_state_mod = pytest.importorskip(
    "docking.applets.todayinhistory.state",
    reason="Today in History applet helpers are not available in this checkout",
)

TodayInHistoryApplet = today_applet_mod.TodayInHistoryApplet
HistoryEvent = today_state_mod.HistoryEvent
fallback_today_in_history = today_state_mod.fallback_today_in_history
fetch_today_in_history = today_state_mod.fetch_today_in_history
format_history_event = today_state_mod.format_history_event
normalize_text = today_state_mod.normalize_text

ENTRY = HistoryEvent(
    year=1969,
    title="Apollo 11 lands on the Moon",
    summary="Neil Armstrong and Buzz Aldrin land in the Sea of Tranquility.",
    article_title="Apollo 11",
    article_url="https://en.wikipedia.org/wiki/Apollo_11",
)


class TestHistoryHelpers:
    def test_normalize_text_and_header_only_format(self):
        assert normalize_text(" Tom &amp;\nJerry ") == "Tom & Jerry"
        assert format_history_event(HistoryEvent(1, "Title", "")) == "1 - Title"

    def test_format_history_event_includes_year_and_content(self):
        text = format_history_event(ENTRY)

        assert "1969" in text
        assert "Apollo 11" in text
        assert "Sea of Tranquility" in text

    @patch("docking.applets.todayinhistory.state.http_get_json")
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

    @patch(
        "docking.applets.todayinhistory.state.http_get_json",
        side_effect=RuntimeError,
    )
    def test_fetch_failure_returns_empty(self, _mock_get):
        assert fetch_today_in_history(month=7, day=20) == []

    def test_fallback_returns_entries_for_any_date(self):
        entries = fallback_today_in_history(month=3, day=14)

        assert entries
        assert all(isinstance(entry, HistoryEvent) for entry in entries)

    def test_low_level_state_parsing_edges(self):
        assert today_state_mod._date_key(month=0, day=0) == "01-01"
        assert today_state_mod._coerce_year(True) is None
        assert today_state_mod._coerce_year(1.9) == 1
        assert today_state_mod._coerce_year(" 44 ") == 44
        assert today_state_mod._coerce_year("bad") is None
        assert today_state_mod._coerce_year(object()) is None
        assert today_state_mod._extract_article("bad") == ("", "")

        title, url = today_state_mod._extract_article(
            {
                "normalizedtitle": "Fallback title",
                "content_urls": {"mobile": {"page": " https://m.example.test "}},
            }
        )
        assert title == "Fallback title"
        assert url == "https://m.example.test"

    def test_event_from_mapping_filters_invalid_and_normalizes(self):
        assert today_state_mod._event_from_mapping({}, default_source="Offline") is None
        assert (
            today_state_mod._event_from_mapping(
                {"year": 1, "title": "", "summary": "x"},
                default_source="Offline",
            )
            is None
        )

        event = today_state_mod._event_from_mapping(
            {
                "year": "1969",
                "title": " Apollo &amp; 11 ",
                "summary": " Moon landing ",
                "article_title": 123,
                "article_url": 123,
                "source_label": 123,
            },
            default_source="Offline",
        )

        assert event == HistoryEvent(
            year=1969,
            title="Apollo & 11",
            summary="Moon landing",
            source_label="Offline",
        )

    def test_parse_wikipedia_events_filters_and_limits(self):
        events = today_state_mod._parse_wikipedia_events(
            data={
                "events": [
                    "bad",
                    {"year": True, "text": "bad"},
                    {"year": 1, "text": ""},
                    {"year": 2, "text": "Only summary title."},
                    {
                        "year": 3,
                        "text": "Page title event.",
                        "pages": [{"title": "Page title"}],
                    },
                ]
            },
            limit=1,
        )

        assert events == [
            HistoryEvent(
                year=2,
                title="Only summary title",
                summary="Only summary title.",
                article_title="Only summary title",
            )
        ]
        assert today_state_mod._parse_wikipedia_events(data=[], limit=3) == []
        assert today_state_mod._parse_wikipedia_events(data={}, limit=3) == []

    def test_fallback_catalog_exact_pool_and_empty(self):
        catalog = {
            "03-14": [ENTRY],
            "03-15": [HistoryEvent(1900, "Other", "Other summary")],
        }

        assert fallback_today_in_history(month=3, day=14, catalog=catalog) == [ENTRY]
        assert fallback_today_in_history(month=1, day=1, catalog={"01-01": []}) == []
        assert len(fallback_today_in_history(month=3, day=16, catalog=catalog)) == 2


class TestTodayInHistoryApplet:
    def test_creates_with_icon_and_initial_event(self):
        applet = TodayInHistoryApplet(48, config=Config())

        assert applet.item.icon is not None
        assert applet._current is not None

    def test_start_and_stop_timer(self, monkeypatch):
        applet = TodayInHistoryApplet(48, config=Config())
        fetch = MagicMock()
        removed: list[int] = []
        monkeypatch.setattr(applet, "_fetch_async", fetch)
        monkeypatch.setattr(
            today_applet_mod.GLib,
            "timeout_add_seconds",
            lambda *_args: 42,
        )
        monkeypatch.setattr(
            today_applet_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )

        applet.start(lambda: None)
        applet.stop()

        fetch.assert_called_once_with(show_first=False)
        assert removed == [42]
        assert applet._timer_id == 0

    def test_click_without_events_fetches(self, monkeypatch):
        applet = TodayInHistoryApplet(48, config=Config())
        applet._events = []
        fetch = MagicMock()
        monkeypatch.setattr(applet, "_fetch_async", fetch)

        applet.on_clicked()

        assert applet._current is None
        fetch.assert_called_once_with(show_first=True)

    def test_click_advances_and_wraps_events(self):
        applet = TodayInHistoryApplet(48, config=Config())
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
        applet = TodayInHistoryApplet(48, config=Config())
        applet._current = ENTRY

        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]

        assert "Wikipedia" in labels
        assert "Next Event" in labels
        assert "Refresh Now" in labels
        assert "Open Article" in labels

    def test_menu_omits_open_article_without_url(self):
        applet = TodayInHistoryApplet(48, config=Config())
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

    def test_source_label_date_and_tooltip_defaults(self):
        applet = TodayInHistoryApplet(48, config=Config())
        applet._current = None
        assert applet._date_key(month=3, day=4) == "03-04"
        assert applet._source_label() == "Today in History"

        applet.refresh_tooltip()
        assert applet.item.name == "Today in History"

    def test_on_fetch_result_uses_fallback_when_api_returns_empty(self, monkeypatch):
        applet = TodayInHistoryApplet(48, config=Config())
        applet._current_month = 7
        applet._current_day = 20
        applet._current = None
        applet._loading = True
        applet._loading_key = "07-20"
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

    def test_on_fetch_result_ignores_stale_day_results(self):
        applet = TodayInHistoryApplet(48, config=Config())
        current = HistoryEvent(
            year=2001,
            title="Current day event",
            summary="Current local day.",
            article_title="Current day",
            article_url="https://example.com/current",
        )
        stale = HistoryEvent(
            year=1999,
            title="Stale day event",
            summary="Previous local day.",
            article_title="Stale day",
            article_url="https://example.com/stale",
        )
        applet._current_month = 3
        applet._current_day = 15
        applet._events = [current]
        applet._index = 0
        applet._current = current
        applet._loading = True
        applet._loading_key = "03-15"

        assert (
            applet._on_fetch_result(month=3, day=14, entries=[stale], show_first=True)
            is False
        )

        assert (applet._current_month, applet._current_day) == (3, 15)
        assert applet._current == current
        assert applet._events == [current]
        assert applet._loading is True
        assert applet._loading_key == "03-15"

    def test_on_fetch_result_entries_keep_current_when_not_show_first(self):
        applet = TodayInHistoryApplet(48, config=Config())
        applet._current_month = 7
        applet._current_day = 20
        current = HistoryEvent(1, "Current", "Current summary")
        new = HistoryEvent(2, "New", "New summary")
        applet._current = current
        applet._loading = True
        applet._loading_key = "07-20"

        applet._on_fetch_result(month=7, day=20, entries=[new], show_first=False)

        assert applet._events == [new]
        assert applet._current == current
        assert applet._loading is False

    def test_on_fetch_result_empty_keeps_existing_current(self, monkeypatch):
        applet = TodayInHistoryApplet(48, config=Config())
        applet._current_month = 7
        applet._current_day = 20
        current = HistoryEvent(1, "Current", "Current summary")
        applet._current = current
        monkeypatch.setattr(
            today_applet_mod,
            "fallback_today_in_history",
            lambda month, day: [ENTRY],
        )

        applet._on_fetch_result(month=7, day=20, entries=[], show_first=False)

        assert applet._current == current

    def test_refresh_from_web_syncs_to_new_local_day_immediately(self, monkeypatch):
        applet = TodayInHistoryApplet(48, config=Config())
        old_entry = HistoryEvent(
            year=1900,
            title="Old day event",
            summary="Before midnight.",
            article_title="Old day",
            article_url="https://example.com/old",
        )
        new_entry = HistoryEvent(
            year=1901,
            title="New day event",
            summary="After midnight.",
            article_title="New day",
            article_url="https://example.com/new",
        )
        applet._current_month = 3
        applet._current_day = 14
        applet._events = [old_entry]
        applet._index = 0
        applet._current = old_entry
        monkeypatch.setattr(applet, "_current_date", lambda: (3, 15))
        monkeypatch.setattr(
            today_applet_mod,
            "fallback_today_in_history",
            lambda month, day: [new_entry] if (month, day) == (3, 15) else [old_entry],
        )

        started: list[object] = []

        class _FakeThread:
            def __init__(self, *, target, daemon):
                self.target = target
                self.daemon = daemon

            def start(self) -> None:
                started.append(self)

        monkeypatch.setattr(today_applet_mod.threading, "Thread", _FakeThread)

        applet._refresh_from_web()

        assert (applet._current_month, applet._current_day) == (3, 15)
        assert applet._current == new_entry
        assert applet._loading is True
        assert applet._loading_key == "03-15"
        assert "1901" in applet.item.name
        assert started

    def test_sync_to_local_day_no_change_and_poll_day_change(self, monkeypatch):
        applet = TodayInHistoryApplet(48, config=Config())
        applet._current_month = 3
        applet._current_day = 14
        monkeypatch.setattr(applet, "_current_date", lambda: (3, 14))
        assert applet._sync_to_local_day() is False
        assert applet._poll_day_change() is True

        monkeypatch.setattr(applet, "_current_date", lambda: (3, 15))
        monkeypatch.setattr(
            today_applet_mod,
            "fallback_today_in_history",
            lambda month, day: [ENTRY],
        )
        applet._fetch_async = MagicMock()
        applet.present = MagicMock()

        assert applet._poll_day_change() is True
        applet.present.assert_called_once()
        applet._fetch_async.assert_called_once_with(show_first=False)

    def test_advance_event_without_events(self):
        applet = TodayInHistoryApplet(48, config=Config())
        applet._events = []
        applet._advance_event()

        assert applet._index == -1
        assert applet._current is None

    def test_fetch_async_dedupes_same_loading_request(self, monkeypatch):
        applet = TodayInHistoryApplet(48, config=Config())
        applet._current_month = 7
        applet._current_day = 20
        applet._loading = True
        applet._loading_key = "07-20"
        monkeypatch.setattr(applet, "_current_date", lambda: (7, 20))
        monkeypatch.setattr(
            today_applet_mod.threading,
            "Thread",
            MagicMock(side_effect=AssertionError("no new thread")),
        )

        applet._fetch_async(show_first=True)

    def test_fetch_async_worker_posts_idle_result(self, monkeypatch):
        applet = TodayInHistoryApplet(48, config=Config())
        applet._current_month = 7
        applet._current_day = 20
        idle_calls = []
        monkeypatch.setattr(applet, "_current_date", lambda: (7, 20))
        monkeypatch.setattr(
            today_applet_mod,
            "fetch_today_in_history",
            lambda **_kwargs: [ENTRY],
        )
        monkeypatch.setattr(
            today_applet_mod.GLib,
            "idle_add",
            lambda *args: idle_calls.append(args),
        )

        class _Thread:
            def __init__(self, *, target, daemon):
                self.target = target
                self.daemon = daemon

            def start(self) -> None:
                self.target()

        monkeypatch.setattr(today_applet_mod.threading, "Thread", _Thread)

        applet._fetch_async(show_first=True)

        assert idle_calls[0][0] == applet._on_fetch_result
        assert idle_calls[0][1:4] == (7, 20, [ENTRY])

    def test_refresh_tooltip_loading_state(self):
        applet = TodayInHistoryApplet(48, config=Config())
        applet._loading = True
        applet._current = None

        applet.refresh_tooltip()

        assert "loading" in applet.item.name.lower()

    def test_open_current_article_noop_success_and_error(self, monkeypatch):
        applet = TodayInHistoryApplet(48, config=Config())
        applet._current = None
        applet._open_current_article()

        applet._current = HistoryEvent(1, "No URL", "Summary")
        applet._open_current_article()

        opened: list[str] = []
        applet._current = ENTRY
        monkeypatch.setattr(
            today_applet_mod.Gio.AppInfo,
            "launch_default_for_uri",
            lambda url, _ctx: opened.append(url),
        )
        applet._open_current_article()
        assert opened == [ENTRY.article_url]

        monkeypatch.setattr(
            today_applet_mod.Gio.AppInfo,
            "launch_default_for_uri",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        applet._open_current_article()
