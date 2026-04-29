"""Tests for the Hacker News applet."""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import docking.applets.hackernews.applet as hackernews_applet_mod
from docking.applets.hackernews.applet import HackerNewsApplet
from docking.applets.hackernews.render import render_icon
from docking.applets.hackernews.state import (
    HN_BASE_URL,
    HN_SOURCE,
    HN_WEB_URL,
    MAX_STORIES,
    HackerNewsPage,
    HackerNewsPrefs,
    HackerNewsStory,
    append_unique_stories,
    build_tooltip,
    comments_url,
    fetch_hn_stories,
    fetch_hn_story_page,
    normalize_active_index,
    normalize_text,
    parse_story_payload,
    parse_top_story_ids,
    prefs_from_mapping,
    prefs_payload,
    story_age,
)
from docking.core.config import Config


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


class _PendingWorker:
    def __init__(self) -> None:
        self.calls = []

    def run(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _story(**overrides: object) -> HackerNewsStory:
    data = {
        "id": 123,
        "title": "SQLite on the Edge",
        "url": "https://example.test/sqlite",
        "hn_url": "https://news.ycombinator.com/item?id=123",
        "score": 456,
        "comments": 78,
        "by": "alice",
        "time": int(
            (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).timestamp()
        ),
        "source": HN_SOURCE,
    }
    data.update(overrides)
    return HackerNewsStory(**data)  # type: ignore[arg-type]


def _make_applet(config: Config | None = None) -> HackerNewsApplet:
    with patch(
        "docking.applets.hackernews.applet.BackgroundWorker",
        _ImmediateWorker,
    ):
        return HackerNewsApplet(48, config=config)


class TestHackerNewsState:
    def test_normalize_text_decodes_entities_and_whitespace(self):
        assert normalize_text("Tom &amp;\n Jerry") == "Tom & Jerry"

    def test_comments_url(self):
        assert comments_url(123) == f"{HN_WEB_URL}/item?id=123"

    def test_parse_top_story_ids(self):
        assert parse_top_story_ids([1, "2", "bad", 3], limit=3) == (1, 2, 3)
        assert parse_top_story_ids([1, 2, 3, 4], limit=2, offset=2) == (3, 4)
        assert parse_top_story_ids("bad", limit=3) == ()

    def test_append_unique_stories(self):
        first = _story(id=1, title="First")
        second = _story(id=2, title="Second")
        duplicate = _story(id=1, title="Duplicate")

        assert append_unique_stories(
            existing=(first,),
            additions=(duplicate, second),
        ) == (first, second)

    def test_parse_story_payload(self):
        story = parse_story_payload(
            {
                "id": 123,
                "type": "story",
                "title": "Tom &amp; Jerry",
                "url": "https://example.test",
                "score": 42,
                "descendants": 7,
                "by": "alice",
                "time": 1000,
            }
        )

        assert story is not None
        assert story.title == "Tom & Jerry"
        assert story.url == "https://example.test"
        assert story.hn_url == f"{HN_WEB_URL}/item?id=123"
        assert story.score == 42
        assert story.comments == 7

    def test_parse_story_payload_rejects_dead_and_non_story_items(self):
        assert parse_story_payload({"id": 1, "type": "comment", "title": "x"}) is None
        assert (
            parse_story_payload({"id": 1, "type": "story", "title": "x", "dead": True})
            is None
        )

    def test_story_without_url_opens_comments(self):
        story = parse_story_payload({"id": 99, "type": "story", "title": "Ask HN"})

        assert story is not None
        assert story.url == f"{HN_WEB_URL}/item?id=99"

    def test_story_age(self):
        story = _story(time=1000)
        now = dt.datetime.fromtimestamp(1000 + 3 * 3600, tz=dt.timezone.utc)

        assert story_age(story=story, now=now) == "3h ago"

    def test_build_tooltip_states(self):
        assert "loading" in build_tooltip(
            story=None,
            index=0,
            count=0,
            loading=True,
        )
        assert "network down" in build_tooltip(
            story=None,
            index=0,
            count=0,
            error="network down",
        )
        text = build_tooltip(story=_story(), index=1, count=3)
        assert "Hacker News 2/3" in text
        assert "SQLite on the Edge" in text
        assert "456 points" in text
        assert "Loading next stories" in build_tooltip(
            story=_story(),
            index=2,
            count=3,
            page_loading=True,
        )

    def test_prefs_round_trip(self):
        story = _story()
        payload = prefs_payload(
            stories=(story,),
            active_index=4,
            next_offset=20,
            has_more_stories=True,
        )
        prefs = prefs_from_mapping(payload)

        assert prefs.stories == (story,)
        assert prefs.active_index == 0
        assert prefs.next_offset == 20
        assert prefs.has_more_stories is True
        assert prefs.fetched_at

    def test_prefs_without_cursor_fields_are_ignored(self):
        prefs = prefs_from_mapping(
            {
                "stories": [
                    {
                        "id": i,
                        "title": f"Story {i}",
                        "url": f"https://example.test/{i}",
                        "hn_url": f"https://news.ycombinator.com/item?id={i}",
                    }
                    for i in range(1, 40)
                ],
                "active_index": 38,
            }
        )

        assert prefs == HackerNewsPrefs()

    def test_bad_prefs_return_empty(self):
        assert prefs_from_mapping(None) == HackerNewsPrefs()
        assert prefs_from_mapping({"stories": [{"id": "bad"}]}).stories == ()

    def test_normalize_active_index(self):
        assert normalize_active_index(index=5, count=2) == 1
        assert normalize_active_index(index=-2, count=2) == 0
        assert normalize_active_index(index=5, count=0) == 0


class TestFetchHnStories:
    def test_fetches_top_stories(self):
        seen = []

        def get_json(url: str):
            seen.append(url)
            if url == f"{HN_BASE_URL}/topstories.json":
                return [111, 222, 123, 456]
            if url == f"{HN_BASE_URL}/item/123.json":
                return {
                    "id": 123,
                    "type": "story",
                    "title": "First",
                    "score": 10,
                }
            return {
                "id": 456,
                "type": "story",
                "title": "Second",
                "score": 20,
            }

        stories = fetch_hn_stories(limit=2, offset=2, get_json=get_json)

        assert [story.title for story in stories] == ["First", "Second"]
        assert seen == [
            f"{HN_BASE_URL}/topstories.json",
            f"{HN_BASE_URL}/item/123.json",
            f"{HN_BASE_URL}/item/456.json",
        ]

    def test_fetch_story_page_tracks_cursor_past_filtered_items(self):
        def get_json(url: str):
            if url == f"{HN_BASE_URL}/topstories.json":
                return [1, 2, 3]
            if url == f"{HN_BASE_URL}/item/1.json":
                return {"id": 1, "type": "story", "title": "First"}
            if url == f"{HN_BASE_URL}/item/2.json":
                return {"id": 2, "type": "comment", "title": "Ignored"}
            return {"id": 3, "type": "story", "title": "Third"}

        page = fetch_hn_story_page(limit=2, offset=0, get_json=get_json)

        assert [story.id for story in page.stories] == [1]
        assert page.next_offset == 2
        assert page.has_more is True

    def test_fetch_failure_returns_empty(self):
        assert (
            fetch_hn_stories(get_json=lambda _url: (_ for _ in ()).throw(OSError()))
            == ()
        )


class TestHackerNewsRender:
    def test_render_icon_states(self):
        for kwargs in (
            {"story": _story(), "index": 1, "count": 3},
            {"loading": True},
            {"error": True},
        ):
            pixbuf = render_icon(size=48, **kwargs)
            assert pixbuf is not None
            assert pixbuf.get_width() == 48
            assert pixbuf.get_height() == 48


class TestHackerNewsApplet:
    def test_loads_cached_stories_from_config(self):
        story = _story()
        config = Config(
            applet_prefs={
                "hackernews": prefs_payload(
                    stories=(story,),
                    active_index=0,
                    next_offset=20,
                    has_more_stories=True,
                )
            }
        )

        applet = _make_applet(config=config)

        assert applet._current_story == story
        assert applet.item.icon is not None
        assert "SQLite on the Edge" in applet.item.name

    def test_scroll_cycles_stories(self):
        applet = _make_applet()
        applet._stories = [_story(id=1, title="First"), _story(id=2, title="Second")]
        applet._active_index = 0

        applet.on_scroll(direction_up=False)

        assert applet._current_story is not None
        assert applet._current_story.title == "Second"

    def test_backward_scroll_at_first_story_stays_at_first(self):
        applet = _make_applet()
        applet._stories = [_story(id=1, title="First"), _story(id=2, title="Second")]
        applet._active_index = 0

        applet.on_scroll(direction_up=True)

        assert applet._current_story is not None
        assert applet._current_story.title == "First"

    def test_arriving_at_last_story_fetches_next_page(self):
        applet = _make_applet()
        applet._stories = [_story(id=i, title=f"Story {i}") for i in range(1, 21)]
        applet._active_index = 18
        applet._next_story_offset = 20
        applet._has_more_stories = True

        with patch(
            "docking.applets.hackernews.applet.fetch_hn_story_page",
            return_value=HackerNewsPage(
                stories=(_story(id=21, title="Story 21"),),
                next_offset=40,
                has_more=True,
            ),
        ) as fetch:
            applet.on_scroll(direction_up=False)

        fetch.assert_called_once_with(limit=20, offset=20)
        assert applet._active_index == 19
        assert applet._next_story_offset == 40
        assert applet._has_more_stories is True
        assert len(applet._stories) == 21
        assert applet._stories[-1].title == "Story 21"

    def test_forward_from_last_waits_for_next_page(self):
        applet = _make_applet()
        worker = _PendingWorker()
        applet._worker = worker
        applet._stories = [_story(id=i, title=f"Story {i}") for i in range(1, 21)]
        applet._active_index = 19
        applet._has_more_stories = True

        applet.on_scroll(direction_up=False)

        assert applet._active_index == 19
        assert applet._page_loading is True
        assert len(worker.calls) == 1
        assert "Loading next stories" in applet.item.name

    def test_next_page_fetch_deduplicates_stories(self):
        applet = _make_applet()
        applet._stories = [_story(id=1, title="First")]
        applet._fetch_request_id = 7

        result = applet._on_page_fetch_result(
            request_id=7,
            page=HackerNewsPage(
                stories=(
                    _story(id=1, title="Duplicate"),
                    _story(id=2, title="Second"),
                ),
                next_offset=20,
                has_more=True,
            ),
        )

        assert result is False
        assert [story.id for story in applet._stories] == [1, 2]
        assert applet._next_story_offset == 20

    def test_next_page_fetch_stops_when_empty(self):
        applet = _make_applet()
        applet._stories = [_story()]
        applet._fetch_request_id = 7
        applet._page_loading = True

        result = applet._on_page_fetch_result(
            request_id=7,
            page=HackerNewsPage(stories=(), next_offset=20, has_more=False),
        )

        assert result is False
        assert applet._page_loading is False
        assert applet._has_more_stories is False

    def test_next_page_can_continue_after_short_visible_page(self):
        applet = _make_applet()
        applet._stories = [_story(id=i, title=f"Story {i}") for i in range(1, 21)]
        applet._fetch_request_id = 7

        applet._on_page_fetch_result(
            request_id=7,
            page=HackerNewsPage(
                stories=tuple(_story(id=i, title=f"Story {i}") for i in range(21, 40)),
                next_offset=40,
                has_more=True,
            ),
        )

        assert len(applet._stories) == 39
        assert applet._next_story_offset == 40
        assert applet._has_more_stories is True

    def test_next_page_caps_visible_stories_at_100(self):
        applet = _make_applet()
        applet._stories = [_story(id=i, title=f"Story {i}") for i in range(1, 96)]
        applet._fetch_request_id = 7

        applet._on_page_fetch_result(
            request_id=7,
            page=HackerNewsPage(
                stories=tuple(_story(id=i, title=f"Story {i}") for i in range(96, 116)),
                next_offset=120,
                has_more=True,
            ),
        )

        assert len(applet._stories) == MAX_STORIES
        assert applet._stories[-1].id == 100
        assert applet._has_more_stories is False

    def test_click_opens_current_story(self):
        applet = _make_applet()
        applet._stories = [_story(url="https://example.test/story")]
        applet._open_url = MagicMock()

        applet.on_clicked()

        applet._open_url.assert_called_once_with("https://example.test/story")

    def test_menu_contains_hn_actions(self):
        applet = _make_applet()
        applet._stories = [_story()]

        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]

        assert "Hacker News" in labels
        assert "Open Story" in labels
        assert "Open Comments" in labels
        assert "Next Headline" in labels
        assert "Refresh Now" in labels

    def test_start_schedules_timers(self, monkeypatch):
        add = MagicMock(side_effect=[11, 12])
        monkeypatch.setattr(
            hackernews_applet_mod.GLib,
            "timeout_add_seconds",
            add,
        )
        applet = _make_applet()

        applet.start(lambda: None)

        assert applet._refresh_timer_id == 11
        assert applet._startup_fetch_timer_id == 12
        assert add.call_count == 2

    def test_fetch_result_replaces_cache_and_saves(self):
        saved = []
        config = Config(applet_prefs={})
        config.save = lambda: saved.append(True)  # type: ignore[method-assign]
        applet = _make_applet(config=config)
        applet._fetch_request_id = 7

        assert (
            applet._on_fetch_result(
                request_id=7,
                page=HackerNewsPage(stories=(_story(),), next_offset=20, has_more=True),
            )
            is False
        )

        assert applet._loading is False
        assert applet._current_story is not None
        assert applet._current_story.title == "SQLite on the Edge"
        assert applet._next_story_offset == 20
        assert saved == [True]

    def test_refresh_fetches_loaded_range_not_only_first_page(self):
        applet = _make_applet()
        applet._stories = [_story(id=i, title=f"Story {i}") for i in range(1, 40)]
        applet._active_index = 38
        applet._next_story_offset = 40

        with patch(
            "docking.applets.hackernews.applet.fetch_hn_story_page",
            return_value=HackerNewsPage(
                stories=tuple(_story(id=i, title=f"Story {i}") for i in range(1, 41)),
                next_offset=40,
                has_more=True,
            ),
        ) as fetch:
            applet._fetch_async()

        fetch.assert_called_once_with(limit=40, offset=0)
        assert applet._active_index == 38
        assert len(applet._stories) == 40

    def test_fetch_result_preserves_current_story(self):
        applet = _make_applet()
        applet._stories = [
            _story(id=1, title="First"),
            _story(id=2, title="Second"),
        ]
        applet._active_index = 1
        applet._fetch_request_id = 7

        applet._on_fetch_result(
            request_id=7,
            page=HackerNewsPage(
                stories=(
                    _story(id=3, title="New"),
                    _story(id=2, title="Second updated"),
                ),
                next_offset=20,
                has_more=True,
            ),
        )

        assert applet._active_index == 1
        assert applet._current_story is not None
        assert applet._current_story.title == "Second updated"

    def test_stale_fetch_result_ignored(self):
        applet = _make_applet()
        applet._fetch_request_id = 7
        applet.present = MagicMock()

        assert (
            applet._on_fetch_result(
                request_id=6,
                page=HackerNewsPage(stories=(_story(),), next_offset=20, has_more=True),
            )
            is False
        )

        applet.present.assert_not_called()

    def test_fetch_error_keeps_cached_stories(self):
        applet = _make_applet()
        applet._fetch_request_id = 7
        applet._stories = [_story()]

        assert applet._on_fetch_error(request_id=7, exc=RuntimeError("boom")) is False

        assert applet._loading is False
        assert applet._error == "boom"
        assert applet._stories
