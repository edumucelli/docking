"""Tests for the public Reddit RSS applet."""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

import docking.applets.reddit.applet as reddit_applet_mod
import docking.applets.reddit.render as reddit_render
import docking.applets.reddit.state as reddit_state
from docking.applets.reddit.applet import RedditApplet
from docking.applets.reddit.render import render_icon
from docking.applets.reddit.state import (
    DEFAULT_SUBREDDITS,
    MAX_SUBREDDITS,
    REDDIT_BASE_URL,
    REDDIT_USER_AGENT,
    RedditPost,
    RedditPrefs,
    RedditSort,
    RedditTopPeriod,
    add_subreddit,
    build_tooltip,
    feed_url,
    fetch_reddit_posts,
    normalize_active_index,
    normalize_subreddit,
    normalize_subreddits,
    parse_reddit_feed,
    post_age,
    post_from_pref,
    post_rank,
    post_to_pref,
    prefs_from_mapping,
    prefs_payload,
    remove_subreddit,
    source_label,
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
        self.calls: list[dict[str, object]] = []

    def run(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _post(**overrides: object) -> RedditPost:
    data: dict[str, object] = {
        "id": "t3_abc123",
        "title": "Linux on the desktop",
        "url": "https://www.reddit.com/r/linux/comments/abc123/example/",
        "author": "alice",
        "published": int(
            (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).timestamp()
        ),
        "subreddit": "linux",
    }
    data.update(overrides)
    return RedditPost(**data)  # type: ignore[arg-type]


def _feed(entries: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>r/linux</title>
      {entries}
    </feed>
    """.encode()


def _entry(
    *,
    post_id: str = "t3_abc123",
    title: str = "Linux &amp; Python",
    url: str = "https://www.reddit.com/r/linux/comments/abc123/example/",
    author: str = "/u/alice",
    published: str = "2026-07-13T10:00:00+00:00",
) -> str:
    return f"""
    <entry>
      <id>{post_id}</id>
      <title>{title}</title>
      <link rel="alternate" href="{url}" />
      <author><name>{author}</name></author>
      <published>{published}</published>
    </entry>
    """


def _make_applet(config: Config | None = None) -> RedditApplet:
    with patch(
        "docking.applets.reddit.applet.BackgroundWorker",
        _ImmediateWorker,
    ):
        return RedditApplet(48, config=config or Config())


class TestRedditSources:
    def test_normalize_subreddit_names_and_urls(self):
        assert normalize_subreddit(" Linux ") == "linux"
        assert normalize_subreddit("r/Python") == "python"
        assert normalize_subreddit("/r/GNOME/") == "gnome"
        assert normalize_subreddit("https://www.reddit.com/r/linux/new/") == "linux"
        assert normalize_subreddit("https://old.reddit.com/r/linux/") == "linux"

    def test_normalize_subreddit_rejects_unsafe_values(self):
        assert normalize_subreddit("") is None
        assert normalize_subreddit("r/a") is None
        assert normalize_subreddit("linux/news") is None
        assert normalize_subreddit("https://example.com/r/linux") is None
        assert normalize_subreddit("https://example.com/reddit.com/r/linux") is None
        assert normalize_subreddit("https://reddit.com.example/r/linux") is None

    def test_subreddit_list_is_unique_bounded_and_has_default(self):
        assert normalize_subreddits("linux") == DEFAULT_SUBREDDITS
        assert normalize_subreddits([]) == DEFAULT_SUBREDDITS
        assert normalize_subreddits(["Linux", "linux", "python"]) == (
            "linux",
            "python",
        )
        values = [f"subreddit_{index}" for index in range(MAX_SUBREDDITS + 5)]
        assert len(normalize_subreddits(values)) == MAX_SUBREDDITS

    def test_add_and_remove_subreddit(self):
        assert add_subreddit(("linux",), subreddit="Python") == (
            "linux",
            "python",
        )
        assert add_subreddit(("linux",), subreddit="linux") == ("linux",)
        assert remove_subreddit(
            ("linux", "python"),
            subreddit="linux",
        ) == ("python",)
        assert remove_subreddit(("linux",), subreddit="linux") == ("linux",)

    @pytest.mark.parametrize(
        ("sort", "expected"),
        [
            (RedditSort.HOT, "/hot/.rss"),
            (RedditSort.NEW, "/new/.rss"),
            (RedditSort.RISING, "/rising/.rss"),
        ],
    )
    def test_feed_url_for_standard_sorts(self, sort, expected):
        url = feed_url(
            subreddit="linux",
            sort=sort,
            top_period=RedditTopPeriod.DAY,
        )

        assert url == f"{REDDIT_BASE_URL}/r/linux{expected}"

    def test_feed_url_for_top_period(self):
        assert (
            feed_url(
                subreddit="linux",
                sort=RedditSort.TOP,
                top_period=RedditTopPeriod.WEEK,
            )
            == f"{REDDIT_BASE_URL}/r/linux/top/.rss?t=week"
        )

    def test_source_label_describes_current_listing(self):
        assert "r/linux" in source_label(
            subreddit="linux",
            sort=RedditSort.HOT,
            top_period=RedditTopPeriod.DAY,
        )
        top = source_label(
            subreddit="linux",
            sort=RedditSort.TOP,
            top_period=RedditTopPeriod.MONTH,
        )
        assert "Top" in top
        assert "This Month" in top


class TestRedditFeedParsing:
    def test_parses_atom_entry(self):
        posts = parse_reddit_feed(
            _feed(_entry()),
            subreddit="linux",
        )

        assert len(posts) == 1
        assert posts[0].id == "t3_abc123"
        assert posts[0].title == "Linux & Python"
        assert posts[0].author == "alice"
        assert posts[0].published > 0
        assert posts[0].subreddit == "linux"

    def test_uses_updated_timestamp_when_published_is_missing(self):
        payload = _feed(
            """
            <entry>
              <id>t3_updated</id>
              <title>Updated</title>
              <link href="https://www.reddit.com/r/linux/comments/updated/" />
              <updated>2026-07-13T11:00:00Z</updated>
            </entry>
            """
        )

        posts = parse_reddit_feed(payload, subreddit="linux")

        assert posts[0].published > 0

    def test_filters_duplicates_invalid_urls_and_empty_titles(self):
        good = _entry()
        duplicate = _entry(title="Duplicate")
        external = _entry(
            post_id="external",
            title="External",
            url="https://example.com/not-reddit",
        )
        empty = _entry(post_id="empty", title="")

        posts = parse_reddit_feed(
            _feed(good + duplicate + external + empty),
            subreddit="linux",
        )

        assert [post.id for post in posts] == ["t3_abc123"]

    def test_limit_and_invalid_payload(self):
        entries = "".join(
            _entry(
                post_id=f"t3_{index}",
                title=f"Post {index}",
                url=f"https://www.reddit.com/r/linux/comments/{index}/",
            )
            for index in range(5)
        )

        assert len(parse_reddit_feed(_feed(entries), subreddit="linux", limit=2)) == 2
        assert parse_reddit_feed(_feed(entries), subreddit="linux", limit=0) == ()
        with pytest.raises(ValueError, match="Invalid Reddit RSS"):
            parse_reddit_feed(b"<not-xml", subreddit="linux")

    def test_fetch_uses_public_feed_and_descriptive_user_agent(self, monkeypatch):
        seen: dict[str, object] = {}

        def get_bytes(url, *, timeout, user_agent):
            seen.update(
                url=url,
                timeout=timeout,
                user_agent=user_agent,
            )
            return _feed(_entry())

        monkeypatch.setattr(reddit_state, "http_get_bytes", get_bytes)

        posts = fetch_reddit_posts(
            subreddit="linux",
            sort=RedditSort.NEW,
            top_period=RedditTopPeriod.DAY,
        )

        assert len(posts) == 1
        assert seen["url"] == f"{REDDIT_BASE_URL}/r/linux/new/.rss"
        assert seen["user_agent"] == REDDIT_USER_AGENT


class TestRedditPreferences:
    def test_post_preference_round_trip_and_validation(self):
        post = _post()
        assert post_from_pref(post_to_pref(post)) == post
        assert post_from_pref("bad") is None
        assert post_from_pref({"id": "x", "title": "x"}) is None
        assert (
            post_from_pref(
                {
                    "id": "x",
                    "title": "x",
                    "url": "https://example.com",
                    "subreddit": "linux",
                }
            )
            is None
        )

    def test_preference_round_trip_keeps_matching_cache(self):
        post = _post()
        payload = prefs_payload(
            subreddits=("linux", "python"),
            active_subreddit_index=0,
            sort=RedditSort.HOT,
            top_period=RedditTopPeriod.DAY,
            posts=(post,),
            active_post_index=0,
            fetched_at=dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc),
        )

        prefs = prefs_from_mapping(payload)

        assert prefs.subreddits == ("linux", "python")
        assert prefs.posts == (post,)
        assert prefs.sort is RedditSort.HOT
        assert prefs.fetched_at

    def test_cache_is_dropped_when_source_settings_do_not_match(self):
        payload = prefs_payload(
            subreddits=("linux",),
            active_subreddit_index=0,
            sort=RedditSort.HOT,
            top_period=RedditTopPeriod.DAY,
            posts=(_post(),),
            active_post_index=0,
            fetched_at=dt.datetime.now(dt.timezone.utc),
        )
        payload["sort"] = "new"

        prefs = prefs_from_mapping(payload)

        assert prefs.sort is RedditSort.NEW
        assert prefs.posts == ()
        assert prefs.fetched_at == ""

    def test_bad_preferences_fall_back_safely(self):
        assert prefs_from_mapping(None) == RedditPrefs()
        prefs = prefs_from_mapping(
            {
                "subreddits": ["bad/name"],
                "active_subreddit_index": "bad",
                "sort": "invalid",
                "top_period": "invalid",
            }
        )
        assert prefs.subreddits == DEFAULT_SUBREDDITS
        assert prefs.sort is RedditSort.HOT
        assert prefs.top_period is RedditTopPeriod.DAY


class TestRedditFormatting:
    def test_rank_index_and_age_edges(self):
        assert post_rank(index=0, count=0) == ""
        assert post_rank(index=1, count=3) == "2/3"
        assert normalize_active_index(index=9, count=2) == 1
        assert normalize_active_index(index=-1, count=2) == 0
        assert post_age(post=_post(published=0)) == ""

        now = dt.datetime.fromtimestamp(1000, tz=dt.timezone.utc)
        assert post_age(post=_post(published=1000), now=now) == "now"
        assert (
            post_age(
                post=_post(published=1000),
                now=now + dt.timedelta(minutes=8),
            )
            == "8m ago"
        )
        assert (
            post_age(
                post=_post(published=1000),
                now=now + dt.timedelta(days=3),
            )
            == "3d ago"
        )

    def test_tooltip_loading_error_and_post_states(self):
        loading = build_tooltip(
            post=None,
            subreddit="linux",
            index=0,
            count=0,
            loading=True,
        )
        error = build_tooltip(
            post=None,
            subreddit="linux",
            index=0,
            count=0,
            error="blocked",
        )
        post = build_tooltip(
            post=_post(),
            subreddit="linux",
            index=1,
            count=3,
            fetched_at=dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc),
            cadence_seconds=10 * 60,
        )

        assert "loading" in loading.lower()
        assert "blocked" in error
        assert "Reddit r/linux 2/3" in post
        assert "Linux on the desktop" in post
        assert "u/alice" in post
        assert "Refreshes every 10 minutes" in post


class TestRedditRendering:
    def test_renders_data_loading_and_error_states(self):
        for size in (32, 48, 64):
            for pixbuf in (
                render_icon(size=size, index=2, count=10),
                render_icon(size=size, loading=True),
                render_icon(size=size, error=True),
            ):
                assert pixbuf is not None
                assert pixbuf.get_width() == size
                assert pixbuf.get_height() == size

    def test_error_uses_shared_warning_badge(self, monkeypatch):
        warning_badge = MagicMock()
        monkeypatch.setattr(
            reddit_render,
            "draw_warning_badge",
            warning_badge,
        )

        pixbuf = render_icon(size=48, error=True)

        assert pixbuf is not None
        warning_badge.assert_called_once()


class TestRedditApplet:
    def test_loads_cached_post_from_config(self):
        post = _post()
        config = Config(
            applet_prefs={
                "reddit": prefs_payload(
                    subreddits=("linux",),
                    active_subreddit_index=0,
                    sort=RedditSort.HOT,
                    top_period=RedditTopPeriod.DAY,
                    posts=(post,),
                    active_post_index=0,
                    fetched_at=dt.datetime.now(dt.timezone.utc),
                )
            }
        )

        applet = _make_applet(config=config)

        assert applet._current_post == post
        assert applet.item.icon is not None
        assert "Linux on the desktop" in applet.item.name

    def test_scroll_moves_and_clamps_posts(self):
        applet = _make_applet()
        applet._posts = [
            _post(id="1", title="First"),
            _post(id="2", title="Second"),
        ]

        applet.on_scroll(direction_up=False)
        assert applet._current_post.title == "Second"
        applet.on_scroll(direction_up=False)
        assert applet._current_post.title == "Second"
        applet.on_scroll(direction_up=True)
        assert applet._current_post.title == "First"

    def test_click_opens_current_reddit_page(self, monkeypatch):
        applet = _make_applet()
        applet._posts = [_post()]
        launch = MagicMock()
        monkeypatch.setattr(
            reddit_applet_mod.Gio.AppInfo,
            "launch_default_for_uri",
            launch,
        )

        applet.on_clicked()

        launch.assert_called_once_with(_post().url, None)

    def test_menu_contains_navigation_source_and_management(self):
        applet = _make_applet()
        applet._posts = [_post()]

        labels = [item.get_label() for item in applet.get_menu_items()]

        assert "Open Post" in labels
        assert "Previous Headline" in labels
        assert "Next Headline" in labels
        assert "Subreddit" in labels
        assert "Sort" in labels
        assert "Add Subreddit..." in labels
        assert "Remove r/linux" in labels
        assert "Refresh Now" in labels

    def test_top_sort_adds_period_submenu(self):
        applet = _make_applet()
        applet._sort = RedditSort.TOP

        labels = [item.get_label() for item in applet.get_menu_items()]

        assert "Top Period" in labels

    def test_source_change_fetches_and_updates_posts(self):
        applet = _make_applet()
        second = _post(
            id="python",
            title="Python post",
            subreddit="python",
            url="https://www.reddit.com/r/python/comments/python/",
        )
        applet._subreddits = ["linux", "python"]

        with patch(
            "docking.applets.reddit.applet.fetch_reddit_posts",
            return_value=(second,),
        ) as fetch:
            applet._set_subreddit(index=1)

        fetch.assert_called_once_with(
            subreddit="python",
            sort=RedditSort.HOT,
            top_period=RedditTopPeriod.DAY,
        )
        assert applet._current_post == second

    def test_add_duplicate_and_remove_sources(self):
        applet = _make_applet()
        with patch(
            "docking.applets.reddit.applet.fetch_reddit_posts",
            return_value=(),
        ):
            applet._add_and_select_subreddit(subreddit="python")
            applet._add_and_select_subreddit(subreddit="python")
            assert applet._subreddits == ["linux", "python"]
            applet._remove_current_subreddit()

        assert applet._subreddits == ["linux"]

    def test_fetch_error_preserves_cached_posts(self):
        applet = _make_applet()
        cached = _post()
        applet._posts = [cached]
        applet._fetch_request_id = 3

        result = applet._on_fetch_error(
            request_id=3,
            exc=OSError("blocked"),
        )

        assert result is False
        assert applet._current_post == cached
        assert applet._error == "blocked"

    def test_empty_fetch_keeps_cache_and_sets_error(self):
        applet = _make_applet()
        cached = _post()
        applet._posts = [cached]
        applet._fetch_request_id = 2

        applet._on_fetch_result(
            request_id=2,
            subreddit="linux",
            posts=(),
        )

        assert applet._current_post == cached
        assert applet._error == "No Reddit posts returned"

    def test_stale_fetch_result_is_ignored(self, monkeypatch):
        applet = _make_applet()
        applet._fetch_request_id = 5
        present = MagicMock()
        monkeypatch.setattr(applet, "present", present)

        result = applet._on_fetch_result(
            request_id=4,
            subreddit="linux",
            posts=(_post(),),
        )

        assert result is False
        assert applet._posts == []
        present.assert_not_called()

    def test_start_and_stop_manage_timers(self, monkeypatch):
        applet = _make_applet()
        timer_ids = iter((101, 202))
        monkeypatch.setattr(
            reddit_applet_mod.GLib,
            "timeout_add_seconds",
            lambda _seconds, _callback: next(timer_ids),
        )
        removed: list[int] = []
        monkeypatch.setattr(
            reddit_applet_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )

        applet.start(lambda: None)
        applet.stop()

        assert removed == [101, 202]

    def test_save_prefs_persists_source_and_cache(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        applet = _make_applet(config=Config.load(path))
        applet._posts = [_post()]
        applet._fetched_at = dt.datetime.now(dt.timezone.utc)

        applet._save_prefs()

        saved = Config.load(path).applet_prefs["reddit"]
        assert saved["subreddits"] == ["linux"]
        assert saved["posts"][0]["title"] == "Linux on the desktop"

    def test_pending_worker_receives_one_fetch(self, monkeypatch):
        applet = _make_applet()
        worker = _PendingWorker()
        monkeypatch.setattr(applet, "_worker", worker)

        applet._fetch_async()

        assert len(worker.calls) == 1
        assert applet._loading
