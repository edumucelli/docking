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
    HackerNewsFeed,
    HackerNewsPage,
    HackerNewsPrefs,
    HackerNewsStory,
    append_unique_stories,
    build_tooltip,
    comments_url,
    feed_endpoint,
    feed_label,
    fetch_hn_stories,
    fetch_hn_story_page,
    normalize_active_index,
    normalize_text,
    parse_story_payload,
    parse_top_story_id_page,
    parse_top_story_ids,
    prefs_from_mapping,
    prefs_payload,
    story_age,
    story_from_pref,
    story_rank,
    story_to_pref,
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
        return HackerNewsApplet(48, config=config or Config())


class TestHackerNewsState:
    def test_normalize_text_decodes_entities_and_whitespace(self):
        assert normalize_text("Tom &amp;\n Jerry") == "Tom & Jerry"

    def test_comments_url(self):
        assert comments_url(123) == f"{HN_WEB_URL}/item?id=123"

    def test_supported_feeds(self):
        assert [feed_label(feed) for feed in HackerNewsFeed] == [
            "Top Stories",
            "Show HN",
            "Jobs",
        ]
        assert [feed_endpoint(feed) for feed in HackerNewsFeed] == [
            "topstories",
            "showstories",
            "jobstories",
        ]

    def test_story_rank_and_age_edges(self):
        assert story_rank(index=0, count=0) == ""
        assert story_age(story=_story(time=0)) == ""
        now = dt.datetime.fromtimestamp(1000, tz=dt.timezone.utc)
        assert story_age(story=_story(time=1000), now=now) == "now"
        assert (
            story_age(
                story=_story(time=1000),
                now=now + dt.timedelta(minutes=10),
            )
            == "10m ago"
        )
        assert (
            story_age(
                story=_story(time=1000),
                now=now + dt.timedelta(days=3),
            )
            == "3d ago"
        )

    def test_parse_top_story_ids(self):
        assert parse_top_story_ids([1, "2", "bad", 3], limit=3) == (1, 2, 3)
        assert parse_top_story_ids([1, 2, 3, 4], limit=2, offset=2) == (3, 4)
        assert parse_top_story_ids("bad", limit=3) == ()
        assert parse_top_story_ids([1, 2], limit=0) == ()

    def test_parse_top_story_id_page_edges(self):
        assert parse_top_story_id_page("bad", limit=2, offset=-1) == ((), 0, False)
        assert parse_top_story_id_page([1, 2], limit=0, offset=5) == ((), 5, False)
        assert parse_top_story_id_page([0, "bad", 1, "2", 3], limit=2, offset=1) == (
            (2, 3),
            3,
            False,
        )

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

    def test_parse_job_payload(self):
        story = parse_story_payload(
            {
                "id": 456,
                "type": "job",
                "title": "Example is hiring",
                "url": "https://example.test/jobs",
                "time": 1000,
            }
        )

        assert story is not None
        assert story.title == "Example is hiring"
        assert story.score == 0
        assert story.comments == 0

    def test_parse_story_payload_rejects_dead_and_non_story_items(self):
        assert parse_story_payload("bad") is None
        assert parse_story_payload({"id": "bad", "type": "story", "title": "x"}) is None
        assert parse_story_payload({"id": 0, "type": "story", "title": "x"}) is None
        assert parse_story_payload({"id": 1, "type": "story", "title": ""}) is None
        assert (
            parse_story_payload(
                {"id": 1, "type": "story", "title": "x", "deleted": True}
            )
            is None
        )
        assert parse_story_payload({"id": 1, "type": "comment", "title": "x"}) is None
        assert (
            parse_story_payload({"id": 1, "type": "story", "title": "x", "dead": True})
            is None
        )

    def test_parse_story_payload_bad_numeric_fields_clamp(self):
        story = parse_story_payload(
            {
                "id": 123,
                "type": "story",
                "title": "Story",
                "score": "bad",
                "descendants": -3,
                "time": "bad",
            }
        )

        assert story is not None
        assert story.score == 0
        assert story.comments == 0
        assert story.time == 0

    def test_story_without_url_opens_comments(self):
        story = parse_story_payload({"id": 99, "type": "story", "title": "Ask HN"})

        assert story is not None
        assert story.url == f"{HN_WEB_URL}/item?id=99"

    def test_story_age(self):
        story = _story(time=1000)
        now = dt.datetime.fromtimestamp(1000 + 3 * 3600, tz=dt.timezone.utc)

        assert story_age(story=story, now=now) == "3h ago"

    def test_build_tooltip_states(self):
        assert (
            "loading"
            in build_tooltip(
                story=None,
                index=0,
                count=0,
                loading=True,
            ).lower()
        )
        assert "network down" in build_tooltip(
            story=None,
            index=0,
            count=0,
            error="network down",
        )
        text = build_tooltip(
            story=_story(),
            index=1,
            count=3,
            fetched_at=dt.datetime(2026, 4, 27, tzinfo=dt.timezone.utc),
            cadence_seconds=10 * 60,
        )
        assert "Hacker News - Top Stories 2/3" in text
        assert "SQLite on the Edge" in text
        assert "456 points" in text
        assert "Updated:" in text
        assert "Refreshes every 10 minutes" in text
        assert "Loading next stories" in build_tooltip(
            story=_story(),
            index=2,
            count=3,
            page_loading=True,
        )

    def test_prefs_round_trip(self):
        story = _story()
        payload = prefs_payload(
            feed=HackerNewsFeed.SHOW,
            stories=(story,),
            active_index=4,
            next_offset=20,
            has_more_stories=True,
        )
        prefs = prefs_from_mapping(payload)

        assert prefs.feed is HackerNewsFeed.SHOW
        assert prefs.stories == (story,)
        assert prefs.active_index == 0
        assert prefs.next_offset == 20
        assert prefs.has_more_stories is True
        assert prefs.fetched_at

    def test_story_pref_edges(self):
        story = _story(id=42, title="Story")
        payload = story_to_pref(story)

        assert story_from_pref(payload) == story
        assert story_from_pref("bad") is None
        assert story_from_pref({"id": "bad", "title": "Story"}) is None
        assert story_from_pref({"id": 0, "title": "Story"}) is None
        assert story_from_pref({"id": 42, "title": ""}) is None
        assert story_from_pref({"id": 42, "title": "Story"}) == _story(
            id=42,
            title="Story",
            url=f"{HN_WEB_URL}/item?id=42",
            hn_url=f"{HN_WEB_URL}/item?id=42",
            score=0,
            comments=0,
            by="",
            time=0,
        )

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
        assert prefs_from_mapping(
            {
                "stories": "bad",
                "active_index": "bad",
                "next_offset": "bad",
                "has_more_stories": False,
            }
        ) == HackerNewsPrefs(has_more_stories=False)
        assert (
            prefs_from_mapping(
                {
                    "feed": "unknown",
                    "stories": [],
                    "next_offset": 0,
                    "has_more_stories": True,
                }
            ).feed
            is HackerNewsFeed.TOP
        )

    def test_normalize_active_index(self):
        assert normalize_active_index(index=5, count=2) == 1
        assert normalize_active_index(index=-2, count=2) == 0
        assert normalize_active_index(index=5, count=0) == 0


class TestFetchHnStories:
    def test_fetches_top_stories(self, monkeypatch):
        import docking.applets.hackernews.state as hn_state

        seen = []

        def get_json(url, *, timeout=None):
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

        monkeypatch.setattr(hn_state, "http_get_json", get_json)
        stories = fetch_hn_stories(limit=2, offset=2)

        assert [story.title for story in stories] == ["First", "Second"]
        assert seen == [
            f"{HN_BASE_URL}/topstories.json",
            f"{HN_BASE_URL}/item/123.json",
            f"{HN_BASE_URL}/item/456.json",
        ]

    def test_fetch_story_page_tracks_cursor_past_filtered_items(self, monkeypatch):
        import docking.applets.hackernews.state as hn_state

        def get_json(url, *, timeout=None):
            if url == f"{HN_BASE_URL}/topstories.json":
                return [1, 2, 3]
            if url == f"{HN_BASE_URL}/item/1.json":
                return {"id": 1, "type": "story", "title": "First"}
            if url == f"{HN_BASE_URL}/item/2.json":
                return {"id": 2, "type": "comment", "title": "Ignored"}
            return {"id": 3, "type": "story", "title": "Third"}

        monkeypatch.setattr(hn_state, "http_get_json", get_json)
        page = fetch_hn_story_page(limit=2, offset=0)

        assert [story.id for story in page.stories] == [1]
        assert page.next_offset == 2
        assert page.has_more is True

    def test_fetches_selected_feed(self, monkeypatch):
        import docking.applets.hackernews.state as hn_state

        seen = []

        def get_json(url, *, timeout=None):
            seen.append(url)
            if url == f"{HN_BASE_URL}/showstories.json":
                return [123]
            return {"id": 123, "type": "story", "title": "Show HN: Example"}

        monkeypatch.setattr(hn_state, "http_get_json", get_json)

        page = fetch_hn_story_page(feed=HackerNewsFeed.SHOW)

        assert [story.title for story in page.stories] == ["Show HN: Example"]
        assert seen == [
            f"{HN_BASE_URL}/showstories.json",
            f"{HN_BASE_URL}/item/123.json",
        ]

    def test_fetch_failure_returns_empty(self, monkeypatch):
        import docking.applets.hackernews.state as hn_state

        def raising(_url, *, timeout=None):
            raise OSError("boom")

        monkeypatch.setattr(hn_state, "http_get_json", raising)
        assert fetch_hn_stories() == ()


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

    def test_scroll_without_stories_is_noop(self):
        applet = _make_applet()

        applet.on_scroll(direction_up=False)

        assert applet._active_index == 0

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

        fetch.assert_called_once_with(
            feed=HackerNewsFeed.TOP,
            limit=20,
            offset=20,
        )
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

    def test_next_page_fetch_ignores_stale_request(self):
        applet = _make_applet()
        applet._fetch_request_id = 7
        applet.present = MagicMock()

        assert (
            applet._on_page_fetch_result(
                request_id=6,
                page=HackerNewsPage(stories=(_story(),), next_offset=20, has_more=True),
            )
            is False
        )
        applet.present.assert_not_called()

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

    def test_next_page_fetch_duplicate_only_presents_without_save(self):
        applet = _make_applet()
        applet._stories = [_story(id=1)]
        applet._fetch_request_id = 7
        applet._save_prefs = MagicMock()

        applet._on_page_fetch_result(
            request_id=7,
            page=HackerNewsPage(
                stories=(_story(id=1),),
                next_offset=20,
                has_more=True,
            ),
        )

        applet._save_prefs.assert_not_called()

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

    def test_open_current_story_and_comments_noop_without_story(self):
        applet = _make_applet()
        applet._open_url = MagicMock()

        applet._open_current_story()
        applet._open_current_comments()

        applet._open_url.assert_not_called()

    def test_open_current_comments_and_target_failure(self, monkeypatch):
        applet = _make_applet()
        applet._stories = [_story(hn_url="https://news.ycombinator.com/item?id=1")]
        open_target = MagicMock(return_value=True)
        monkeypatch.setattr(hackernews_applet_mod.targets, "open_target", open_target)

        applet._open_current_comments()

        open_target.assert_called_once_with("https://news.ycombinator.com/item?id=1")

        open_target.return_value = False
        applet._open_url("https://example.test")

        assert open_target.call_args_list[-1].args == ("https://example.test",)

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
        assert "Refreshes every 10 minutes" in labels
        assert "Refresh Now" in labels
        assert "Page" in labels

        page = next(
            item for item in applet.get_menu_items() if item.get_label() == "Page"
        )
        submenu = page.get_submenu()
        assert submenu is not None
        assert [item.get_label() for item in submenu.children] == [
            "Top Stories",
            "Show HN",
            "Jobs",
        ]

    def test_switching_feed_clears_cache_and_fetches_selected_page(self):
        applet = _make_applet()
        applet._stories = [_story()]
        applet._next_story_offset = 20

        with patch(
            "docking.applets.hackernews.applet.fetch_hn_story_page",
            return_value=HackerNewsPage(
                stories=(_story(id=456, title="A job"),),
                next_offset=1,
                has_more=False,
            ),
        ) as fetch:
            applet._set_feed(feed=HackerNewsFeed.JOBS)

        fetch.assert_called_once_with(
            feed=HackerNewsFeed.JOBS,
            limit=20,
            offset=0,
        )
        assert applet._feed is HackerNewsFeed.JOBS
        assert [story.title for story in applet._stories] == ["A job"]
        assert applet._next_story_offset == 1

    def test_selecting_current_feed_is_noop(self):
        applet = _make_applet()
        applet._fetch_async = MagicMock()

        applet._set_feed(feed=HackerNewsFeed.TOP)

        applet._fetch_async.assert_not_called()

    def test_menu_without_story_disables_navigation(self):
        applet = _make_applet()

        items = applet.get_menu_items()
        next_item = next(item for item in items if item.get_label() == "Next Headline")

        assert not next_item.get_sensitive()

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

    def test_stop_removes_timers(self, monkeypatch):
        applet = _make_applet()
        applet._refresh_timer_id = 11
        applet._startup_fetch_timer_id = 12
        removed: list[int] = []
        monkeypatch.setattr(
            hackernews_applet_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )

        applet.stop()

        assert removed == [11, 12]
        assert applet._refresh_timer_id == 0
        assert applet._startup_fetch_timer_id == 0

    def test_refresh_and_startup_ticks_fetch(self, monkeypatch):
        applet = _make_applet()
        calls: list[str] = []
        monkeypatch.setattr(applet, "_fetch_async", lambda: calls.append("fetch"))

        assert applet._refresh_tick() is True
        applet._startup_fetch_timer_id = 99
        assert applet._run_startup_fetch() is False

        assert applet._startup_fetch_timer_id == 0
        assert calls == ["fetch", "fetch"]

    def test_advance_and_move_story_edge_cases(self):
        applet = _make_applet()
        applet._advance_story()
        applet._move_story(step=1)

        applet._stories = [_story(id=1), _story(id=2)]
        applet._active_index = 1
        applet._has_more_stories = False
        applet.present = MagicMock()
        applet._move_story(step=1)
        applet.present.assert_called_once()

        applet._active_index = 0
        applet.present.reset_mock()
        applet._move_story(step=-1)
        applet.present.assert_called_once()

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

    def test_set_active_index_saves_and_presents(self):
        applet = _make_applet()
        applet._stories = [_story(id=1), _story(id=2)]
        applet._save_prefs = MagicMock()
        applet.present = MagicMock()

        applet._set_active_index(9)

        assert applet._active_index == 1
        applet._save_prefs.assert_called_once()
        applet.present.assert_called_once()

    def test_fetch_async_removes_startup_timer_and_queues_worker(
        self,
        monkeypatch,
    ):
        applet = _make_applet()
        worker = _PendingWorker()
        applet._worker = worker
        applet._startup_fetch_timer_id = 77
        removed: list[int] = []
        monkeypatch.setattr(
            hackernews_applet_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )

        applet._fetch_async()

        assert removed == [77]
        assert applet._startup_fetch_timer_id == 0
        assert applet._loading is True
        assert worker.calls[0]["name"] == "hackernews-fetch"

    def test_fetch_result_empty_page_sets_error_only_without_cache(self):
        applet = _make_applet()
        applet._fetch_request_id = 7

        applet._on_fetch_result(
            request_id=7,
            page=HackerNewsPage(stories=(), next_offset=0, has_more=False),
        )

        assert "No Hacker News stories" in applet._error

    def test_fetch_error_stale_and_empty_exception(self):
        applet = _make_applet()
        applet._fetch_request_id = 7
        applet.present = MagicMock()

        assert applet._on_fetch_error(request_id=6, exc=RuntimeError("old")) is False
        applet.present.assert_not_called()

        assert applet._on_fetch_error(request_id=7, exc=RuntimeError()) is False
        assert applet._error == "RuntimeError"

    def test_refreshed_active_index_edge_cases(self):
        applet = _make_applet()
        assert (
            applet._refreshed_active_index(current_story=_story(), previous_index=99)
            == 0
        )

        applet._stories = [_story(id=1), _story(id=2)]
        assert (
            applet._refreshed_active_index(
                current_story=_story(id=3),
                previous_index=99,
            )
            == 1
        )

    def test_fetch_next_page_guards_and_max_cap(self):
        applet = _make_applet()
        applet._stories = [_story(id=i) for i in range(MAX_STORIES)]
        applet._has_more_stories = True
        applet.present = MagicMock()

        applet._fetch_next_page_async()

        assert applet._has_more_stories is False
        applet.present.assert_called_once()

        applet._stories = [_story(id=1)]
        for loading, page_loading, has_more in (
            (True, False, True),
            (False, True, True),
            (False, False, False),
        ):
            applet._loading = loading
            applet._page_loading = page_loading
            applet._has_more_stories = has_more
            applet._worker = _PendingWorker()
            applet._fetch_next_page_async()
            assert applet._worker.calls == []

    def test_fetch_next_page_queues_worker(self):
        applet = _make_applet()
        worker = _PendingWorker()
        applet._worker = worker
        applet._stories = [_story(id=1)]
        applet._next_story_offset = 20
        applet._has_more_stories = True

        applet._fetch_next_page_async()

        assert applet._page_loading is True
        assert applet._fetch_request_id == 1
        assert worker.calls[0]["name"] == "hackernews-page-fetch"

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

        fetch.assert_called_once_with(
            feed=HackerNewsFeed.TOP,
            limit=40,
            offset=0,
        )
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
