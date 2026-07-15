"""Pure RSS, preference, and formatting helpers for the Reddit applet."""

from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, cast
from urllib.parse import quote, urlsplit

from docking.applets.http import http_get_bytes
from docking.applets.live_state import (
    live_freshness_lines,
    live_state_error,
    live_state_label,
    refresh_recovery_label,
    resolve_live_status,
)
from docking.applets.text import normalize_text
from docking.applets.tooltip import structured_tooltip
from docking.core.math import clamp_index
from docking.i18n import _

REDDIT_BASE_URL = "https://www.reddit.com"
REDDIT_SOURCE_LABEL = "Reddit"
REDDIT_USER_AGENT = (
    "linux:cc.docking.Docking:1.0 (+https://github.com/edumucelli/docking)"
)
ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
DEFAULT_SUBREDDITS = ("linux",)
DEFAULT_FETCH_LIMIT = 25
MAX_CACHED_POSTS = 25
MAX_SUBREDDITS = 20
FETCH_TIMEOUT_S = 8
REFRESH_INTERVAL_S = 10 * 60
STARTUP_FETCH_DELAY_S = 2

_SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{2,32}$")


class RedditSort(str, Enum):
    """Supported public Reddit RSS listing orders."""

    HOT = "hot"
    NEW = "new"
    TOP = "top"
    RISING = "rising"


class RedditTopPeriod(str, Enum):
    """Supported periods for top listings."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class RedditPost:
    """One public post parsed from Reddit's Atom feed."""

    id: str
    title: str
    url: str
    author: str
    published: int
    subreddit: str


@dataclass(frozen=True, slots=True)
class RedditPrefs:
    """Persisted source selection and startup cache."""

    subreddits: tuple[str, ...] = DEFAULT_SUBREDDITS
    active_subreddit_index: int = 0
    sort: RedditSort = RedditSort.HOT
    top_period: RedditTopPeriod = RedditTopPeriod.DAY
    posts: tuple[RedditPost, ...] = ()
    active_post_index: int = 0
    cache_subreddit: str = ""
    cache_sort: str = ""
    cache_top_period: str = ""
    fetched_at: str = ""


def normalize_subreddit(value: object) -> str | None:
    """Normalize a subreddit name or public subreddit URL."""
    text = normalize_text(value).strip()
    if not text:
        return None
    parsed = urlsplit(text)
    if parsed.scheme in {"http", "https"} and parsed.hostname in {
        "reddit.com",
        "www.reddit.com",
        "old.reddit.com",
    }:
        path = parsed.path
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2 and parts[0].casefold() == "r":
            text = parts[1]
    text = text.removeprefix("/").removeprefix("r/").strip("/")
    if not _SUBREDDIT_RE.fullmatch(text):
        return None
    return text.casefold()


def normalize_subreddits(value: object) -> tuple[str, ...]:
    """Return a bounded, unique subreddit tuple."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return DEFAULT_SUBREDDITS
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        subreddit = normalize_subreddit(raw)
        if subreddit is None or subreddit in seen:
            continue
        result.append(subreddit)
        seen.add(subreddit)
        if len(result) >= MAX_SUBREDDITS:
            break
    return tuple(result) or DEFAULT_SUBREDDITS


def add_subreddit(
    subreddits: Sequence[str],
    *,
    subreddit: str,
) -> tuple[str, ...]:
    """Append one valid, unique subreddit."""
    normalized = normalize_subreddit(subreddit)
    current = normalize_subreddits(tuple(subreddits))
    if normalized is None or normalized in current or len(current) >= MAX_SUBREDDITS:
        return current
    return (*current, normalized)


def remove_subreddit(
    subreddits: Sequence[str],
    *,
    subreddit: str,
) -> tuple[str, ...]:
    """Remove one subreddit while retaining at least one source."""
    current = normalize_subreddits(tuple(subreddits))
    if len(current) <= 1:
        return current
    kept = tuple(item for item in current if item != subreddit)
    return kept or current


def sort_label(sort: RedditSort) -> str:
    """Return a translated listing-order label."""
    labels = {
        RedditSort.HOT: _("Hot"),
        RedditSort.NEW: _("New"),
        RedditSort.TOP: _("Top"),
        RedditSort.RISING: _("Rising"),
    }
    return labels[sort]


def top_period_label(period: RedditTopPeriod) -> str:
    """Return a translated top-period label."""
    labels = {
        RedditTopPeriod.DAY: _("Today"),
        RedditTopPeriod.WEEK: _("This Week"),
        RedditTopPeriod.MONTH: _("This Month"),
        RedditTopPeriod.YEAR: _("This Year"),
        RedditTopPeriod.ALL: _("All Time"),
    }
    return labels[period]


def feed_url(
    *,
    subreddit: str,
    sort: RedditSort,
    top_period: RedditTopPeriod,
) -> str:
    """Build a public Reddit Atom URL from validated preferences."""
    normalized = normalize_subreddit(subreddit)
    if normalized is None:
        raise ValueError(_("Invalid subreddit name"))
    path = f"{REDDIT_BASE_URL}/r/{quote(normalized)}/{sort.value}/.rss"
    if sort is RedditSort.TOP:
        return f"{path}?t={top_period.value}"
    return path


def source_label(
    *,
    subreddit: str,
    sort: RedditSort,
    top_period: RedditTopPeriod,
) -> str:
    """Return the compact current-source description."""
    listing = sort_label(sort)
    if sort is RedditSort.TOP:
        listing = _("{sort}, {period}").format(
            sort=listing,
            period=top_period_label(top_period),
        )
    return _("r/{subreddit} - {listing}").format(
        subreddit=subreddit,
        listing=listing,
    )


def parse_reddit_feed(
    payload: bytes | str,
    *,
    subreddit: str,
    limit: int = DEFAULT_FETCH_LIMIT,
) -> tuple[RedditPost, ...]:
    """Parse one public Reddit Atom response."""
    if limit <= 0:
        return ()
    try:
        root = ET.fromstring(payload)
    except (ET.ParseError, TypeError) as exc:
        raise ValueError(_("Invalid Reddit RSS response")) from exc

    namespace = f"{{{ATOM_NAMESPACE}}}"
    posts: list[RedditPost] = []
    seen: set[str] = set()
    for entry in root.findall(f"{namespace}entry"):
        title = normalize_text(entry.findtext(f"{namespace}title"))
        url = _entry_url(entry=entry, namespace=namespace)
        post_id = normalize_text(entry.findtext(f"{namespace}id")) or url
        if not title or not post_id or not _is_reddit_url(url):
            continue
        if post_id in seen:
            continue
        author_node = entry.find(f"{namespace}author")
        author = (
            normalize_text(author_node.findtext(f"{namespace}name"))
            if author_node is not None
            else ""
        )
        published_text = (
            entry.findtext(f"{namespace}published")
            or entry.findtext(f"{namespace}updated")
            or ""
        )
        posts.append(
            RedditPost(
                id=post_id,
                title=title,
                url=url,
                author=_normalize_author(author),
                published=_parse_timestamp(published_text),
                subreddit=subreddit,
            )
        )
        seen.add(post_id)
        if len(posts) >= min(limit, MAX_CACHED_POSTS):
            break
    return tuple(posts)


def fetch_reddit_posts(
    *,
    subreddit: str,
    sort: RedditSort,
    top_period: RedditTopPeriod,
    limit: int = DEFAULT_FETCH_LIMIT,
) -> tuple[RedditPost, ...]:
    """Fetch and parse one public Reddit RSS listing."""
    url = feed_url(
        subreddit=subreddit,
        sort=sort,
        top_period=top_period,
    )
    payload = http_get_bytes(
        url,
        timeout=FETCH_TIMEOUT_S,
        user_agent=REDDIT_USER_AGENT,
    )
    return parse_reddit_feed(payload, subreddit=subreddit, limit=limit)


def post_rank(*, index: int, count: int) -> str:
    """Return the selected post position."""
    if count <= 0:
        return ""
    return f"{index + 1}/{count}"


def normalize_active_index(*, index: int, count: int) -> int:
    """Clamp the active post or subreddit index."""
    if count <= 0:
        return 0
    return clamp_index(index, count)


def post_age(*, post: RedditPost, now: dt.datetime | None = None) -> str:
    """Return a compact relative publication age."""
    if post.published <= 0:
        return ""
    current = now or dt.datetime.now(dt.timezone.utc)
    published = dt.datetime.fromtimestamp(post.published, tz=dt.timezone.utc)
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
    post: RedditPost | None,
    subreddit: str,
    index: int,
    count: int,
    loading: bool = False,
    error: str = "",
    fetched_at: dt.datetime | str | None = None,
    cadence_seconds: int | None = None,
) -> str:
    """Build the current Reddit applet tooltip."""
    status = resolve_live_status(
        has_data=post is not None,
        loading=loading,
        error=error,
        updated_at=fetched_at,
    )
    rank = post_rank(index=index, count=count)
    title = f"{REDDIT_SOURCE_LABEL} r/{subreddit}"
    if rank:
        title = f"{title} {rank}"
    if post is None:
        return structured_tooltip(
            title=title,
            primary=live_state_label(status),
            freshness=live_freshness_lines(
                status=status,
                updated_at=fetched_at,
                cadence_seconds=cadence_seconds,
                cadence_verb=_("Refreshes"),
            ),
            error=live_state_error(status=status, error=error),
            recovery=refresh_recovery_label(status),
        )

    details: list[str] = []
    if post.author:
        details.append(_("Posted by u/{author}").format(author=post.author))
    age = post_age(post=post)
    if age:
        details.append(age)
    return structured_tooltip(
        title=title,
        primary=post.title,
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


def post_to_pref(post: RedditPost) -> dict[str, object]:
    """Serialize one cached post."""
    return {
        "id": post.id,
        "title": post.title,
        "url": post.url,
        "author": post.author,
        "published": post.published,
        "subreddit": post.subreddit,
    }


def post_from_pref(value: object) -> RedditPost | None:
    """Parse one cached post."""
    if not isinstance(value, Mapping):
        return None
    raw = cast(Mapping[str, Any], value)
    post_id = normalize_text(raw.get("id"))
    title = normalize_text(raw.get("title"))
    url = normalize_text(raw.get("url"))
    subreddit = normalize_subreddit(raw.get("subreddit"))
    if not post_id or not title or not _is_reddit_url(url) or subreddit is None:
        return None
    return RedditPost(
        id=post_id,
        title=title,
        url=url,
        author=_normalize_author(normalize_text(raw.get("author"))),
        published=max(0, _int_from_value(raw.get("published"), 0)),
        subreddit=subreddit,
    )


def prefs_from_mapping(prefs: Mapping[str, Any] | None) -> RedditPrefs:
    """Load Reddit preferences and matching cached posts."""
    if not prefs:
        return RedditPrefs()
    subreddits = normalize_subreddits(prefs.get("subreddits"))
    active_subreddit_index = normalize_active_index(
        index=_int_from_value(prefs.get("active_subreddit_index"), 0),
        count=len(subreddits),
    )
    sort = _enum_from_value(
        RedditSort,
        prefs.get("sort"),
        RedditSort.HOT,
    )
    top_period = _enum_from_value(
        RedditTopPeriod,
        prefs.get("top_period"),
        RedditTopPeriod.DAY,
    )
    cache_subreddit = normalize_subreddit(prefs.get("cache_subreddit")) or ""
    cache_sort = normalize_text(prefs.get("cache_sort"))
    cache_top_period = normalize_text(prefs.get("cache_top_period"))
    active_subreddit = subreddits[active_subreddit_index]
    cache_matches = (
        cache_subreddit == active_subreddit
        and cache_sort == sort.value
        and cache_top_period == top_period.value
    )

    posts: list[RedditPost] = []
    raw_posts = prefs.get("posts")
    if (
        cache_matches
        and isinstance(raw_posts, Sequence)
        and not isinstance(
            raw_posts,
            str | bytes,
        )
    ):
        for raw_post in raw_posts:
            post = post_from_pref(raw_post)
            if post is not None and post.subreddit == active_subreddit:
                posts.append(post)
            if len(posts) >= MAX_CACHED_POSTS:
                break
    active_post_index = normalize_active_index(
        index=_int_from_value(prefs.get("active_post_index"), 0),
        count=len(posts),
    )
    return RedditPrefs(
        subreddits=subreddits,
        active_subreddit_index=active_subreddit_index,
        sort=sort,
        top_period=top_period,
        posts=tuple(posts),
        active_post_index=active_post_index,
        cache_subreddit=cache_subreddit if cache_matches else "",
        cache_sort=cache_sort if cache_matches else "",
        cache_top_period=cache_top_period if cache_matches else "",
        fetched_at=normalize_text(prefs.get("fetched_at")) if cache_matches else "",
    )


def prefs_payload(
    *,
    subreddits: Sequence[str],
    active_subreddit_index: int,
    sort: RedditSort,
    top_period: RedditTopPeriod,
    posts: Sequence[RedditPost],
    active_post_index: int,
    fetched_at: dt.datetime | None,
) -> dict[str, object]:
    """Build the JSON-safe Reddit preference and cache payload."""
    normalized_subreddits = normalize_subreddits(tuple(subreddits))
    source_index = normalize_active_index(
        index=active_subreddit_index,
        count=len(normalized_subreddits),
    )
    source = normalized_subreddits[source_index]
    kept = tuple(post for post in posts if post.subreddit == source)[:MAX_CACHED_POSTS]
    return {
        "subreddits": list(normalized_subreddits),
        "active_subreddit_index": source_index,
        "sort": sort.value,
        "top_period": top_period.value,
        "active_post_index": normalize_active_index(
            index=active_post_index,
            count=len(kept),
        ),
        "cache_subreddit": source,
        "cache_sort": sort.value,
        "cache_top_period": top_period.value,
        "fetched_at": (
            fetched_at.astimezone(dt.timezone.utc).isoformat()
            if fetched_at is not None
            else ""
        ),
        "posts": [post_to_pref(post) for post in kept],
    }


def _entry_url(*, entry: ET.Element, namespace: str) -> str:
    for link in entry.findall(f"{namespace}link"):
        if link.attrib.get("rel", "alternate") != "alternate":
            continue
        url = normalize_text(link.attrib.get("href"))
        if url:
            return url
    return ""


def _is_reddit_url(value: str) -> bool:
    if not value:
        return False
    parsed = urlsplit(value)
    host = (parsed.hostname or "").casefold()
    return parsed.scheme in {"http", "https"} and (
        host in {"reddit.com", "redd.it"} or host.endswith((".reddit.com", ".redd.it"))
    )


def _normalize_author(value: str) -> str:
    author = value.strip().removeprefix("/").removeprefix("u/")
    return author.strip("/")


def _parse_timestamp(value: str) -> int:
    normalized = value.strip()
    if not normalized:
        return 0
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


def _enum_from_value(enum_type, value: object, fallback):
    try:
        return enum_type(str(value))
    except (TypeError, ValueError):
        return fallback
