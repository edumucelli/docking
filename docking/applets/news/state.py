"""Pure source, RSS, preference, and tooltip helpers for the News applet."""

from __future__ import annotations

import datetime as dt
import email.utils
import ipaddress
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

from docking.applets.http import http_get_bytes
from docking.applets.live_state import (
    live_freshness_lines,
    live_state_error,
    live_state_label,
    refresh_recovery_label,
    resolve_live_status,
)
from docking.applets.news.countries import country_name
from docking.applets.text import normalize_text
from docking.applets.tooltip import structured_tooltip
from docking.core.math import clamp_index
from docking.i18n import _

NEWS_SOURCE_LABEL = "News"
NEWS_USER_AGENT = "DockingNews/1.0 (+https://github.com/edumucelli/docking)"
DEFAULT_FETCH_LIMIT = 25
MAX_CACHED_ARTICLES = 25
MAX_SOURCES = 20
MAX_FEED_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_S = 10
REFRESH_INTERVAL_S = 10 * 60
STARTUP_FETCH_DELAY_S = 2

_COUNTRY_CODE_RE = re.compile(r"^[A-Z0-9_]{2,16}$")
_UNSAFE_XML_RE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class NewsSource:
    """One selectable publication feed from the country catalog."""

    country_code: str
    publication_name: str
    publication_url: str
    feed_url: str
    category: str = ""
    language_code: str = ""
    language_name: str = ""


@dataclass(frozen=True, slots=True)
class NewsArticle:
    """One normalized article from an RSS or Atom feed."""

    id: str
    title: str
    url: str
    author: str
    published: int
    source_feed_url: str


@dataclass(frozen=True, slots=True)
class NewsPrefs:
    """Persisted News sources and the active feed's startup cache."""

    sources: tuple[NewsSource, ...] = ()
    active_source_index: int = 0
    articles: tuple[NewsArticle, ...] = ()
    active_article_index: int = 0
    cache_feed_url: str = ""
    fetched_at: str = ""


def normalize_http_url(value: object) -> str:
    """Return a bounded HTTP(S) URL without credentials, or an empty string."""
    url = normalize_text(value)
    if not url or len(url) > 2048 or any(char.isspace() for char in url):
        return ""
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or not _is_public_hostname(hostname)
    ):
        return ""
    return url


def normalize_country_code(value: object) -> str:
    """Return a supported catalog-style country code or an empty string."""
    country_code = normalize_text(value).upper()
    return country_code if _COUNTRY_CODE_RE.fullmatch(country_code) else ""


def source_from_mapping(value: object) -> NewsSource | None:
    """Parse one source descriptor from catalog or preference data."""
    if not isinstance(value, Mapping):
        return None
    raw = cast(Mapping[str, Any], value)
    country_code = normalize_country_code(raw.get("country_code"))
    publication_name = normalize_text(raw.get("publication_name"))[:300]
    feed_url = normalize_http_url(raw.get("feed_url"))
    publication_url = normalize_http_url(raw.get("publication_url"))
    if not country_code or not publication_name or not feed_url:
        return None
    return NewsSource(
        country_code=country_code,
        publication_name=publication_name,
        publication_url=publication_url,
        feed_url=feed_url,
        category=normalize_text(raw.get("category"))[:200],
        language_code=normalize_text(raw.get("language_code"))[:16],
        language_name=normalize_text(raw.get("language_name"))[:100],
    )


def source_to_pref(source: NewsSource) -> dict[str, str]:
    """Serialize one source descriptor."""
    return {
        "country_code": source.country_code,
        "publication_name": source.publication_name,
        "publication_url": source.publication_url,
        "feed_url": source.feed_url,
        "category": source.category,
        "language_code": source.language_code,
        "language_name": source.language_name,
    }


def normalize_sources(value: object) -> tuple[NewsSource, ...]:
    """Return a bounded, feed-URL-unique source tuple."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    result: list[NewsSource] = []
    seen: set[str] = set()
    for raw in value:
        source = source_from_mapping(
            source_to_pref(raw) if isinstance(raw, NewsSource) else raw
        )
        if source is None or source.feed_url in seen:
            continue
        result.append(source)
        seen.add(source.feed_url)
        if len(result) >= MAX_SOURCES:
            break
    return tuple(result)


def add_source(
    sources: Sequence[NewsSource], *, source: NewsSource
) -> tuple[NewsSource, ...]:
    """Append one unique source while respecting the configured limit."""
    current = normalize_sources(tuple(sources))
    if source.feed_url in {item.feed_url for item in current}:
        return current
    if len(current) >= MAX_SOURCES:
        return current
    return (*current, source)


def remove_source(
    sources: Sequence[NewsSource], *, feed_url: str
) -> tuple[NewsSource, ...]:
    """Remove a source by its stable feed URL, including the final source."""
    return tuple(
        source
        for source in normalize_sources(tuple(sources))
        if source.feed_url != feed_url
    )


def source_label(source: NewsSource) -> str:
    """Return a compact publication label, including a feed category."""
    if source.category:
        return _("{publication} - {category}").format(
            publication=source.publication_name,
            category=source.category,
        )
    return source.publication_name


def source_detail(source: NewsSource) -> str:
    """Describe the source's country and language."""
    parts = [country_name(source.country_code)]
    if source.language_name:
        parts.append(source.language_name)
    return " - ".join(parts)


def parse_news_feed(
    payload: bytes | str,
    *,
    source: NewsSource,
    limit: int = DEFAULT_FETCH_LIMIT,
) -> tuple[NewsArticle, ...]:
    """Parse bounded RSS 2.0, RSS/RDF, or Atom XML into news articles."""
    if limit <= 0:
        return ()
    raw_bytes = payload.encode("utf-8") if isinstance(payload, str) else payload
    if _UNSAFE_XML_RE.search(raw_bytes):
        raise ValueError(_("Unsafe RSS response"))
    try:
        root = ET.fromstring(raw_bytes)
    except (ET.ParseError, TypeError) as exc:
        raise ValueError(_("Invalid RSS response")) from exc

    articles: list[NewsArticle] = []
    seen: set[str] = set()
    for node in root.iter():
        if _local_name(node.tag) not in {"item", "entry"}:
            continue
        title = _child_text(node, ("title",))
        url = _entry_url(node)
        article_id = _child_text(node, ("guid", "id")) or url
        if not title or not url or not article_id or article_id in seen:
            continue
        author = _child_text(node, ("author", "creator"))
        published_text = _child_text(
            node,
            ("published", "updated", "pubdate", "date"),
        )
        articles.append(
            NewsArticle(
                id=article_id[:2048],
                title=title[:500],
                url=url,
                author=author[:200],
                published=_parse_timestamp(published_text),
                source_feed_url=source.feed_url,
            )
        )
        seen.add(article_id)
        if len(articles) >= min(limit, MAX_CACHED_ARTICLES):
            break
    return tuple(articles)


def fetch_news_articles(
    *, source: NewsSource, limit: int = DEFAULT_FETCH_LIMIT
) -> tuple[NewsArticle, ...]:
    """Fetch and parse one configured publication feed."""
    payload = http_get_bytes(
        source.feed_url,
        timeout=FETCH_TIMEOUT_S,
        user_agent=NEWS_USER_AGENT,
        max_bytes=MAX_FEED_BYTES,
    )
    return parse_news_feed(payload, source=source, limit=limit)


def article_rank(*, index: int, count: int) -> str:
    """Return the selected article position."""
    if count <= 0:
        return ""
    return f"{index + 1}/{count}"


def normalize_active_index(*, index: int, count: int) -> int:
    """Clamp a source or article index."""
    if count <= 0:
        return 0
    return clamp_index(index, count)


def article_age(*, article: NewsArticle, now: dt.datetime | None = None) -> str:
    """Return a compact relative publication age."""
    if article.published <= 0:
        return ""
    current = now or dt.datetime.now(dt.timezone.utc)
    published = dt.datetime.fromtimestamp(article.published, tz=dt.timezone.utc)
    seconds = max(0, int((current - published).total_seconds()))
    if seconds < 60:
        return _("now")
    minutes = seconds // 60
    if minutes < 60:
        return _("{minutes}m ago").format(minutes=minutes)
    hours = minutes // 60
    if hours < 48:
        return _("{hours}h ago").format(hours=hours)
    return _("{days}d ago").format(days=hours // 24)


def build_tooltip(
    *,
    source: NewsSource | None,
    article: NewsArticle | None,
    index: int,
    count: int,
    loading: bool = False,
    error: str = "",
    fetched_at: dt.datetime | str | None = None,
    cadence_seconds: int | None = None,
) -> str:
    """Build the current News applet tooltip."""
    if source is None:
        return structured_tooltip(
            title=NEWS_SOURCE_LABEL,
            primary=_("Click to choose a country and news source"),
        )
    status = resolve_live_status(
        has_data=article is not None,
        loading=loading,
        error=error,
        updated_at=fetched_at,
    )
    rank = article_rank(index=index, count=count)
    title = f"{NEWS_SOURCE_LABEL} - {source_label(source)}"
    if rank:
        title = f"{title} {rank}"
    if article is None:
        return structured_tooltip(
            title=title,
            primary=live_state_label(status),
            details=[source_detail(source)],
            freshness=live_freshness_lines(
                status=status,
                updated_at=fetched_at,
                cadence_seconds=cadence_seconds,
                cadence_verb=_("Refreshes"),
            ),
            error=live_state_error(status=status, error=error),
            recovery=refresh_recovery_label(status),
        )

    details = [source_detail(source)]
    if article.author:
        details.append(_("By {author}").format(author=article.author))
    age = article_age(article=article)
    if age:
        details.append(age)
    return structured_tooltip(
        title=title,
        primary=article.title,
        details=details,
        freshness=live_freshness_lines(
            status=status,
            updated_at=fetched_at,
            cadence_seconds=cadence_seconds,
            cadence_verb=_("Refreshes"),
        ),
        error=live_state_error(status=status, error=error),
        recovery=refresh_recovery_label(status),
    )


def article_to_pref(article: NewsArticle) -> dict[str, object]:
    """Serialize one cached article."""
    return {
        "id": article.id,
        "title": article.title,
        "url": article.url,
        "author": article.author,
        "published": article.published,
        "source_feed_url": article.source_feed_url,
    }


def article_from_pref(value: object) -> NewsArticle | None:
    """Parse one cached article."""
    if not isinstance(value, Mapping):
        return None
    raw = cast(Mapping[str, Any], value)
    article_id = normalize_text(raw.get("id"))[:2048]
    title = normalize_text(raw.get("title"))[:500]
    url = normalize_http_url(raw.get("url"))
    feed_url = normalize_http_url(raw.get("source_feed_url"))
    if not article_id or not title or not url or not feed_url:
        return None
    return NewsArticle(
        id=article_id,
        title=title,
        url=url,
        author=normalize_text(raw.get("author"))[:200],
        published=max(0, _int_from_value(raw.get("published"), 0)),
        source_feed_url=feed_url,
    )


def prefs_from_mapping(prefs: Mapping[str, Any] | None) -> NewsPrefs:
    """Load sources and only the active feed's matching article cache."""
    if not prefs:
        return NewsPrefs()
    sources = normalize_sources(prefs.get("sources"))
    active_source_index = normalize_active_index(
        index=_int_from_value(prefs.get("active_source_index"), 0),
        count=len(sources),
    )
    active_feed_url = sources[active_source_index].feed_url if sources else ""
    cache_feed_url = normalize_http_url(prefs.get("cache_feed_url"))
    cache_matches = bool(active_feed_url and cache_feed_url == active_feed_url)
    articles: list[NewsArticle] = []
    raw_articles = prefs.get("articles")
    if (
        cache_matches
        and isinstance(raw_articles, Sequence)
        and not isinstance(raw_articles, str | bytes)
    ):
        for raw in raw_articles:
            article = article_from_pref(raw)
            if article is not None and article.source_feed_url == active_feed_url:
                articles.append(article)
            if len(articles) >= MAX_CACHED_ARTICLES:
                break
    return NewsPrefs(
        sources=sources,
        active_source_index=active_source_index,
        articles=tuple(articles),
        active_article_index=normalize_active_index(
            index=_int_from_value(prefs.get("active_article_index"), 0),
            count=len(articles),
        ),
        cache_feed_url=cache_feed_url if cache_matches else "",
        fetched_at=normalize_text(prefs.get("fetched_at")) if cache_matches else "",
    )


def prefs_payload(
    *,
    sources: Sequence[NewsSource],
    active_source_index: int,
    articles: Sequence[NewsArticle],
    active_article_index: int,
    fetched_at: dt.datetime | None,
) -> dict[str, object]:
    """Build the JSON-safe News preference and active-source cache payload."""
    normalized_sources = normalize_sources(tuple(sources))
    source_index = normalize_active_index(
        index=active_source_index,
        count=len(normalized_sources),
    )
    active_feed_url = (
        normalized_sources[source_index].feed_url if normalized_sources else ""
    )
    kept = tuple(
        article for article in articles if article.source_feed_url == active_feed_url
    )[:MAX_CACHED_ARTICLES]
    return {
        "sources": [source_to_pref(source) for source in normalized_sources],
        "active_source_index": source_index,
        "active_article_index": normalize_active_index(
            index=active_article_index,
            count=len(kept),
        ),
        "cache_feed_url": active_feed_url,
        "fetched_at": (
            fetched_at.astimezone(dt.timezone.utc).isoformat()
            if fetched_at is not None and active_feed_url
            else ""
        ),
        "articles": [article_to_pref(article) for article in kept],
    }


def _local_name(tag: object) -> str:
    text = str(tag)
    return text.rsplit("}", 1)[-1].casefold()


def _element_text(element: ET.Element) -> str:
    return normalize_text(" ".join(element.itertext()))


def _child_text(node: ET.Element, names: tuple[str, ...]) -> str:
    wanted = {name.casefold() for name in names}
    for child in node:
        if _local_name(child.tag) in wanted:
            text = _element_text(child)
            if text:
                return text
    return ""


def _entry_url(node: ET.Element) -> str:
    for child in node:
        if _local_name(child.tag) != "link":
            continue
        rel = normalize_text(child.attrib.get("rel", "alternate")).casefold()
        if rel not in {"", "alternate"}:
            continue
        url = normalize_http_url(child.attrib.get("href") or _element_text(child))
        if url:
            return url
    guid = _child_text(node, ("guid",))
    return normalize_http_url(guid)


def _parse_timestamp(value: str) -> int:
    normalized = value.strip()
    if not normalized:
        return 0
    try:
        parsed = email.utils.parsedate_to_datetime(normalized)
    except (TypeError, ValueError):
        try:
            parsed = dt.datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return max(0, int(parsed.timestamp()))


def _int_from_value(value: object, fallback: int) -> int:
    if not isinstance(value, str | int | float):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _is_public_hostname(hostname: str) -> bool:
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return not hostname.endswith((".local", ".internal"))
    return address.is_global
