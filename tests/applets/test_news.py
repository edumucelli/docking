"""Tests for the country-based News RSS applet."""

from __future__ import annotations

import datetime as dt
import json
import os
from unittest.mock import MagicMock, patch

import pytest

import docking.applets.http as http_mod
import docking.applets.news.applet as news_applet_mod
import docking.applets.news.catalog as catalog_mod
import docking.applets.news.render as news_render
import docking.applets.news.state as news_state
from docking.applets.news.applet import NewsApplet
from docking.applets.news.catalog import (
    CATALOG_TTL,
    CachedNewsCatalog,
    NewsCatalog,
    load_cached_catalog,
    parse_catalog,
    refresh_catalog,
)
from docking.applets.news.countries import (
    country_code_for_locale,
    country_name,
    sorted_country_codes,
)
from docking.applets.news.render import render_icon
from docking.applets.news.state import (
    MAX_SOURCES,
    NEWS_USER_AGENT,
    NewsArticle,
    NewsPrefs,
    NewsSource,
    add_source,
    article_age,
    article_from_pref,
    article_rank,
    article_to_pref,
    build_tooltip,
    fetch_news_articles,
    normalize_active_index,
    normalize_sources,
    parse_news_feed,
    prefs_from_mapping,
    prefs_payload,
    remove_source,
    source_detail,
    source_from_mapping,
    source_label,
    source_to_pref,
)
from docking.core.config import Config


class _ImmediateWorker:
    def __init__(self, **_kwargs) -> None:
        self._active_keys: set[str] = set()

    def run(self, *, fn, on_result=None, on_error=None, **_kwargs) -> None:
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return
        if on_result is not None:
            on_result(result)

    def run_guarded(self, *, key, **kwargs) -> bool:
        if key in self._active_keys:
            return False
        self._active_keys.add(key)
        try:
            self.run(**kwargs)
        finally:
            self._active_keys.discard(key)
        return True


class _PendingWorker:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _source(**overrides: object) -> NewsSource:
    values: dict[str, object] = {
        "country_code": "USA",
        "publication_name": "Example News",
        "publication_url": "https://news.example/",
        "feed_url": "https://news.example/rss.xml",
        "category": "Top Stories",
        "language_code": "en",
        "language_name": "English",
    }
    values.update(overrides)
    return NewsSource(**values)  # type: ignore[arg-type]


def _article(**overrides: object) -> NewsArticle:
    values: dict[str, object] = {
        "id": "article-1",
        "title": "A useful headline",
        "url": "https://news.example/article-1",
        "author": "Alice",
        "published": int(
            (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).timestamp()
        ),
        "source_feed_url": _source().feed_url,
    }
    values.update(overrides)
    return NewsArticle(**values)  # type: ignore[arg-type]


def _catalog_payload() -> bytes:
    return json.dumps(
        {
            "USA": [
                {
                    "publication_name": "Example News",
                    "publication_website_uri": "https://news.example/",
                    "publication_rss_feed_uris": [
                        {
                            "uri": "https://news.example/rss.xml",
                            "category": "Top Stories",
                            "language_code": "en",
                            "language_name": "English",
                        },
                        {
                            "uri": "https://news.example/local.xml",
                            "category": "Local",
                            "language_code": "en",
                            "language_name": "English",
                        },
                    ],
                }
            ],
            "GLOBAL": [
                {
                    "publication_name": "World News",
                    "publication_website_uri": "https://world.example/",
                    "publication_rss_feed_uris": [
                        {
                            "uri": "https://world.example/feed",
                            "language_name": "English",
                        }
                    ],
                }
            ],
        }
    ).encode()


def _rss(items: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Example</title>{items}</channel></rss>
    """.encode()


def _rss_item(
    *,
    guid: str = "article-1",
    title: str = "A useful &amp; clear headline",
    url: str = "https://news.example/article-1",
    author: str = "Alice",
    published: str = "Sun, 19 Jul 2026 10:00:00 +0000",
) -> str:
    return f"""
    <item>
      <guid>{guid}</guid><title>{title}</title><link>{url}</link>
      <author>{author}</author><pubDate>{published}</pubDate>
    </item>
    """


def _make_applet(config: Config | None = None) -> NewsApplet:
    with patch("docking.applets.news.applet.BackgroundWorker", _ImmediateWorker):
        return NewsApplet(48, config=config or Config())


class TestCountries:
    def test_country_names_locale_matching_and_sorting(self):
        assert country_name("USA") == "United States"
        assert country_name("global") == "Global"
        assert country_name("ZZZ") == "ZZZ"
        assert country_code_for_locale("pt_BR.UTF-8") == "BRA"
        assert country_code_for_locale("C") is None
        assert sorted_country_codes({"USA", "GLOBAL", "BRA"}) == (
            "GLOBAL",
            "BRA",
            "USA",
        )


class TestCatalog:
    def test_parses_and_flattens_publication_feeds(self):
        catalog = parse_catalog(_catalog_payload())

        assert catalog.country_codes == ("GLOBAL", "USA")
        assert catalog.source_count == 3
        assert len(catalog.sources_by_country["USA"]) == 2
        assert catalog.sources_by_country["USA"][1].category == "Local"

    def test_deduplicates_and_rejects_private_or_malformed_urls(self):
        payload = json.loads(_catalog_payload())
        feeds = payload["USA"][0]["publication_rss_feed_uris"]
        feeds.extend(
            [
                {"uri": "https://world.example/feed"},
                {"uri": "http://127.0.0.1/private"},
                {"uri": "file:///tmp/feed"},
                "bad",
            ]
        )

        catalog = parse_catalog(json.dumps(payload))

        assert catalog.source_count == 3
        assert all(
            "127.0.0.1" not in source.feed_url
            for sources in catalog.sources_by_country.values()
            for source in sources
        )

    @pytest.mark.parametrize("payload", [b"[]", b"{}", b"not-json"])
    def test_rejects_invalid_or_empty_catalog(self, payload):
        with pytest.raises(ValueError):
            parse_catalog(payload)

    def test_loads_fresh_and_stale_cache(self, tmp_path):
        path = tmp_path / "catalog.json"
        path.write_bytes(_catalog_payload())
        now = dt.datetime.now(dt.timezone.utc)

        fresh = load_cached_catalog(cache_path=path, now=now)
        assert fresh is not None
        assert not fresh.stale

        old = now - CATALOG_TTL - dt.timedelta(seconds=1)
        os.utime(path, (old.timestamp(), old.timestamp()))
        stale = load_cached_catalog(cache_path=path, now=now)
        assert stale is not None
        assert stale.stale

        path.write_text("bad", encoding="utf-8")
        assert load_cached_catalog(cache_path=path, now=now) is None

    def test_refresh_validates_before_replacing_cache(self, tmp_path, monkeypatch):
        path = tmp_path / "catalog.json"
        path.write_bytes(_catalog_payload())
        original = path.read_bytes()
        monkeypatch.setattr(catalog_mod, "fetch_catalog_payload", lambda: b"bad")

        with pytest.raises(ValueError):
            refresh_catalog(cache_path=path)

        assert path.read_bytes() == original

        monkeypatch.setattr(
            catalog_mod,
            "fetch_catalog_payload",
            _catalog_payload,
        )
        refreshed = refresh_catalog(cache_path=path)
        assert refreshed.catalog.source_count == 3
        assert not refreshed.stale

    def test_fetch_catalog_uses_bounded_download(self, monkeypatch):
        get_bytes = MagicMock(return_value=_catalog_payload())
        monkeypatch.setattr(catalog_mod, "http_get_bytes", get_bytes)

        assert catalog_mod.fetch_catalog_payload() == _catalog_payload()

        get_bytes.assert_called_once_with(
            catalog_mod.CATALOG_URL,
            timeout=catalog_mod.CATALOG_TIMEOUT_S,
            user_agent=catalog_mod.CATALOG_USER_AGENT,
            max_bytes=catalog_mod.MAX_CATALOG_BYTES,
        )


class TestFeedParsing:
    def test_parses_rss_and_rfc822_dates(self):
        articles = parse_news_feed(_rss(_rss_item()), source=_source())

        assert len(articles) == 1
        assert articles[0].id == "article-1"
        assert articles[0].title == "A useful & clear headline"
        assert articles[0].author == "Alice"
        assert articles[0].published > 0

    def test_parses_atom_namespaces_and_iso_dates(self):
        payload = b"""
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry><id>atom-1</id><title>Atom Headline</title>
            <link rel="alternate" href="https://news.example/atom" />
            <author><name>Bob</name></author>
            <updated>2026-07-19T11:30:00Z</updated>
          </entry>
        </feed>
        """

        article = parse_news_feed(payload, source=_source())[0]

        assert article.id == "atom-1"
        assert article.author == "Bob"
        assert article.published > 0

    def test_parses_namespaced_rdf_item(self):
        payload = b"""
        <rdf:RDF xmlns:rdf="urn:rdf" xmlns:rss="urn:rss">
          <rss:item><rss:title>RDF Headline</rss:title>
            <rss:link>https://news.example/rdf</rss:link>
            <rss:date>2026-07-19T12:00:00+00:00</rss:date>
          </rss:item>
        </rdf:RDF>
        """

        article = parse_news_feed(payload, source=_source())[0]

        assert article.title == "RDF Headline"
        assert article.id == "https://news.example/rdf"

    def test_filters_duplicates_invalid_entries_and_honors_limit(self):
        good = _rss_item()
        duplicate = _rss_item(title="Duplicate")
        invalid_url = _rss_item(guid="bad", url="file:///tmp/article")
        empty_title = _rss_item(guid="empty", title="")
        second = _rss_item(
            guid="article-2",
            title="Second",
            url="https://news.example/article-2",
        )

        articles = parse_news_feed(
            _rss(good + duplicate + invalid_url + empty_title + second),
            source=_source(),
            limit=1,
        )

        assert [article.id for article in articles] == ["article-1"]
        assert parse_news_feed(_rss(good), source=_source(), limit=0) == ()

    @pytest.mark.parametrize(
        "payload",
        [
            b"<not-xml",
            b'<!DOCTYPE rss [<!ENTITY x "bad">]><rss>&x;</rss>',
        ],
    )
    def test_rejects_invalid_or_unsafe_xml(self, payload):
        with pytest.raises(ValueError):
            parse_news_feed(payload, source=_source())

    def test_fetch_uses_source_url_and_size_limit(self, monkeypatch):
        get_bytes = MagicMock(return_value=_rss(_rss_item()))
        monkeypatch.setattr(news_state, "http_get_bytes", get_bytes)

        articles = fetch_news_articles(source=_source())

        assert len(articles) == 1
        get_bytes.assert_called_once_with(
            _source().feed_url,
            timeout=news_state.FETCH_TIMEOUT_S,
            user_agent=NEWS_USER_AGENT,
            max_bytes=news_state.MAX_FEED_BYTES,
        )


class TestSourcesAndPreferences:
    def test_source_round_trip_and_validation(self):
        source = _source()
        assert source_from_mapping(source_to_pref(source)) == source
        assert source_from_mapping("bad") is None
        assert source_from_mapping({"country_code": "USA"}) is None
        assert (
            source_from_mapping(
                {
                    "country_code": "USA",
                    "publication_name": "Bad",
                    "feed_url": "file:///tmp/feed",
                }
            )
            is None
        )

    def test_normalizes_adds_and_removes_sources(self):
        first = _source()
        second = _source(
            publication_name="Second",
            feed_url="https://second.example/feed",
        )
        raw = [source_to_pref(first), source_to_pref(first), source_to_pref(second)]

        assert normalize_sources(raw) == (first, second)
        assert add_source((first,), source=second) == (first, second)
        assert add_source((first,), source=first) == (first,)
        assert remove_source((first,), feed_url=first.feed_url) == ()

        many = tuple(
            _source(
                publication_name=f"Source {index}",
                feed_url=f"https://source{index}.example/feed",
            )
            for index in range(MAX_SOURCES + 2)
        )
        assert len(normalize_sources(many)) == MAX_SOURCES

    def test_article_round_trip_and_matching_cache(self):
        article = _article()
        assert article_from_pref(article_to_pref(article)) == article
        assert article_from_pref("bad") is None

        payload = prefs_payload(
            sources=(_source(),),
            active_source_index=0,
            articles=(article,),
            active_article_index=0,
            fetched_at=dt.datetime(2026, 7, 19, tzinfo=dt.timezone.utc),
        )
        prefs = prefs_from_mapping(payload)

        assert prefs.sources == (_source(),)
        assert prefs.articles == (article,)
        assert prefs.fetched_at

        payload["cache_feed_url"] = "https://different.example/feed"
        mismatched = prefs_from_mapping(payload)
        assert mismatched.articles == ()
        assert mismatched.fetched_at == ""

    def test_bad_preferences_fall_back_safely(self):
        assert prefs_from_mapping(None) == NewsPrefs()
        prefs = prefs_from_mapping(
            {
                "sources": "bad",
                "active_source_index": "bad",
                "articles": "bad",
            }
        )
        assert prefs == NewsPrefs()


class TestFormattingAndRendering:
    def test_source_labels_rank_index_and_age(self):
        assert source_label(_source()) == "Example News - Top Stories"
        assert source_detail(_source()) == "United States - English"
        assert article_rank(index=0, count=0) == ""
        assert article_rank(index=1, count=3) == "2/3"
        assert normalize_active_index(index=8, count=2) == 1
        assert normalize_active_index(index=-1, count=2) == 0
        assert article_age(article=_article(published=0)) == ""

        now = dt.datetime.fromtimestamp(1000, tz=dt.timezone.utc)
        assert article_age(article=_article(published=1000), now=now) == "now"
        assert (
            article_age(
                article=_article(published=1000),
                now=now + dt.timedelta(minutes=8),
            )
            == "8m ago"
        )

    def test_tooltip_setup_loading_error_and_article_states(self):
        setup = build_tooltip(
            source=None,
            article=None,
            index=0,
            count=0,
        )
        loading = build_tooltip(
            source=_source(),
            article=None,
            index=0,
            count=0,
            loading=True,
        )
        error = build_tooltip(
            source=_source(),
            article=None,
            index=0,
            count=0,
            error="blocked",
        )
        article = build_tooltip(
            source=_source(),
            article=_article(),
            index=1,
            count=3,
            fetched_at=dt.datetime(2026, 7, 19, tzinfo=dt.timezone.utc),
            cadence_seconds=10 * 60,
        )

        assert "choose a country" in setup.lower()
        assert "loading" in loading.lower()
        assert "blocked" in error
        assert "News - Example News - Top Stories 2/3" in article
        assert "A useful headline" in article
        assert "By Alice" in article

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
        monkeypatch.setattr(news_render, "draw_warning_badge", warning_badge)

        assert render_icon(size=48, error=True) is not None
        warning_badge.assert_called_once()


class TestHttpLimit:
    def test_bounded_read_accepts_limit_and_rejects_overflow(self, monkeypatch):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"1234"
        monkeypatch.setattr(http_mod, "urlopen", MagicMock(return_value=response))

        assert http_mod.http_get_bytes("https://example.test", max_bytes=4) == b"1234"
        response.read.assert_called_once_with(5)

        response.read.return_value = b"12345"
        with pytest.raises(ValueError, match="exceeds"):
            http_mod.http_get_bytes("https://example.test", max_bytes=4)


class TestNewsApplet:
    def test_loads_cached_article_from_config(self):
        config = Config(
            applet_prefs={
                "news": prefs_payload(
                    sources=(_source(),),
                    active_source_index=0,
                    articles=(_article(),),
                    active_article_index=0,
                    fetched_at=dt.datetime.now(dt.timezone.utc),
                )
            }
        )

        applet = _make_applet(config)

        assert applet._current_article == _article()
        assert "A useful headline" in applet.item.name
        assert applet.item.icon is not None

    def test_click_configures_refreshes_or_opens_based_on_state(self, monkeypatch):
        applet = _make_applet()
        picker = MagicMock()
        monkeypatch.setattr(applet, "_show_source_picker", picker)
        applet.on_clicked()
        picker.assert_called_once()

        applet._sources = [_source()]
        fetch = MagicMock()
        monkeypatch.setattr(applet, "_fetch_async", fetch)
        applet.on_clicked()
        fetch.assert_called_once()

        applet._articles = [_article()]
        launch = MagicMock()
        monkeypatch.setattr(
            news_applet_mod.Gio.AppInfo, "launch_default_for_uri", launch
        )
        applet.on_clicked()
        launch.assert_called_once_with(_article().url, None)

    def test_scroll_moves_and_clamps_articles(self):
        applet = _make_applet()
        applet._sources = [_source()]
        applet._articles = [
            _article(id="1", title="First"),
            _article(id="2", title="Second"),
        ]

        applet.on_scroll(direction_up=False)
        assert applet._current_article.title == "Second"
        applet.on_scroll(direction_up=False)
        assert applet._current_article.title == "Second"
        applet.on_scroll(direction_up=True)
        assert applet._current_article.title == "First"

    def test_menu_contains_source_navigation_and_management(self):
        second = _source(
            publication_name="Second News",
            feed_url="https://second.example/feed",
        )
        applet = _make_applet()
        applet._sources = [_source(), second]
        applet._articles = [_article()]

        labels = [item.get_label() for item in applet.get_menu_items()]

        assert "Open Headline" in labels
        assert "Open Publication" in labels
        assert "Previous Headline" in labels
        assert "Next Headline" in labels
        assert "Refresh Now" in labels
        assert "Source" in labels
        assert "Add News Source..." in labels
        assert "Remove Current News Source" in labels

    def test_source_change_add_and_remove(self):
        applet = _make_applet()
        applet._sources = [_source()]
        second = _source(
            publication_name="Second News",
            feed_url="https://second.example/feed",
        )
        second_article = _article(
            id="second",
            title="Second headline",
            url="https://second.example/article",
            source_feed_url=second.feed_url,
        )

        with patch(
            "docking.applets.news.applet.fetch_news_articles",
            return_value=(second_article,),
        ):
            applet._add_and_select_source(second)
            applet._add_and_select_source(second)

        assert applet._sources == [_source(), second]
        assert applet._current_article == second_article

        with patch(
            "docking.applets.news.applet.fetch_news_articles",
            return_value=(),
        ):
            applet._remove_current_source()
            applet._remove_current_source()

        assert applet._sources == []
        assert applet._current_source is None

    def test_fetch_failure_or_empty_result_preserves_cache(self):
        applet = _make_applet()
        applet._sources = [_source()]
        applet._articles = [_article()]
        applet._fetch_request_id = 3

        applet._on_fetch_error(request_id=3, exc=OSError("blocked"))
        assert applet._current_article == _article()
        assert applet._error == "blocked"

        applet._loading = True
        applet._on_fetch_result(
            request_id=3,
            feed_url=_source().feed_url,
            articles=(),
        )
        assert applet._current_article == _article()
        assert applet._error == "No news articles returned"

    def test_stale_fetch_result_is_ignored(self, monkeypatch):
        applet = _make_applet()
        applet._sources = [_source()]
        applet._fetch_request_id = 5
        present = MagicMock()
        monkeypatch.setattr(applet, "present", present)

        result = applet._on_fetch_result(
            request_id=4,
            feed_url=_source().feed_url,
            articles=(_article(),),
        )

        assert result is False
        assert applet._articles == []
        present.assert_not_called()

    def test_start_and_stop_manage_timers(self, monkeypatch):
        applet = _make_applet()
        applet._sources = [_source()]
        timer_ids = iter((101, 202))
        monkeypatch.setattr(
            news_applet_mod.GLib,
            "timeout_add_seconds",
            lambda _seconds, _callback: next(timer_ids),
        )
        removed: list[int] = []
        monkeypatch.setattr(
            news_applet_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )

        applet.start(lambda: None)
        applet.stop()

        assert removed == [101, 202]

    def test_pending_worker_receives_one_fetch(self):
        applet = _make_applet()
        applet._sources = [_source()]
        worker = _PendingWorker()
        applet._worker = worker  # type: ignore[assignment]

        applet._fetch_async()

        assert len(worker.calls) == 1
        assert applet._loading

    @pytest.mark.skipif(
        not news_applet_mod.Gtk.init_check()[0],
        reason="GTK display unavailable",
    )
    def test_picker_uses_cached_catalog_and_locale_country(self, monkeypatch):
        applet = _make_applet()
        cached = CachedNewsCatalog(
            catalog=parse_catalog(_catalog_payload()),
            updated_at=dt.datetime.now(dt.timezone.utc),
            stale=False,
        )
        monkeypatch.setattr(news_applet_mod, "load_cached_catalog", lambda: cached)
        monkeypatch.setattr(news_applet_mod, "country_code_for_locale", lambda: "USA")

        applet._show_source_picker()

        assert applet._country_list is not None
        selected = applet._country_list.get_selected_row()
        assert selected is not None
        assert selected.code == "USA"  # type: ignore[attr-defined]
        assert applet._source_list is not None
        assert len(applet._source_list.get_children()) == 2
        applet._source_dialog.destroy()

    def test_catalog_error_keeps_loaded_catalog(self):
        applet = _make_applet()
        applet._catalog = NewsCatalog({"USA": (_source(),)})
        label = MagicMock()
        applet._catalog_status = label

        applet._on_catalog_error(OSError("offline"))

        assert "using cached list" in label.set_text.call_args.args[0]
