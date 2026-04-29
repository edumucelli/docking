"""Hacker News data helpers for the Hacker News applet.

The applet targets the official HN Firebase API and deliberately models that
specific source instead of pretending to be a generic news reader.

Only state, parsing, formatting, and HTTP live here.  GTK timers, menus, and
clipboard/open-url integration stay in ``applet.py`` so this module is easy to
test without a desktop session.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from docking.i18n import _
from docking.log import get_logger

log = get_logger(name="hackernews.state")

HN_BASE_URL = "https://hacker-news.firebaseio.com/v0"
HN_WEB_URL = "https://news.ycombinator.com"
HN_SOURCE = "hackernews"
HN_SOURCE_LABEL = "Hacker News"
FETCH_TIMEOUT_S = 5
DEFAULT_FETCH_LIMIT = 20
MAX_STORIES = 100
MAX_STORIES_IN_PREFS = MAX_STORIES
REFRESH_INTERVAL_S = 10 * 60
STARTUP_FETCH_DELAY_S = 2


@dataclass(frozen=True, slots=True)
class HackerNewsStory:
    """One displayable HN story."""

    id: int
    title: str
    url: str
    hn_url: str
    score: int
    comments: int
    by: str
    time: int
    source: str = HN_SOURCE


@dataclass(frozen=True, slots=True)
class HackerNewsPrefs:
    """Persisted Hacker News preferences.

    ``stories`` is a startup cache so the icon has useful content before the
    first web refresh. ``active_index`` keeps the manually selected headline
    stable across restarts.
    """

    stories: tuple[HackerNewsStory, ...] = ()
    active_index: int = 0
    next_offset: int = 0
    has_more_stories: bool = True
    fetched_at: str = ""


@dataclass(frozen=True, slots=True)
class HackerNewsPage:
    """One fetched page from HN's topstories id list."""

    stories: tuple[HackerNewsStory, ...]
    next_offset: int
    has_more: bool


def normalize_text(value: object) -> str:
    """Normalize API text fields for compact tooltip/menu display."""
    text = html.unescape(str(value or ""))
    return " ".join(text.replace("\n", " ").replace("\r", " ").split()).strip()


def story_rank(*, index: int, count: int) -> str:
    """Human-readable current item position."""
    if count <= 0:
        return ""
    return f"{index + 1}/{count}"


def story_age(*, story: HackerNewsStory, now: dt.datetime | None = None) -> str:
    """Return a short relative age string for an HN story."""
    if story.time <= 0:
        return ""
    current = now or dt.datetime.now(dt.timezone.utc)
    published = dt.datetime.fromtimestamp(story.time, tz=dt.timezone.utc)
    seconds = max(0, int((current - published).total_seconds()))
    if seconds < 60:
        return _("now")
    minutes = seconds // 60
    if minutes < 60:
        return _("{minutes}m ago").format(minutes=minutes)
    hours = minutes // 60
    if hours < 48:
        return _("{hours}h ago").format(hours=hours)
    days = hours // 24
    return _("{days}d ago").format(days=days)


def comments_url(story_id: int) -> str:
    """Return the HN comments URL for a story id."""
    return f"{HN_WEB_URL}/item?id={story_id}"


def normalize_active_index(*, index: int, count: int) -> int:
    """Clamp active index for the current story list."""
    if count <= 0:
        return 0
    return max(0, min(index, count - 1))


def build_tooltip(
    *,
    story: HackerNewsStory | None,
    index: int,
    count: int,
    loading: bool = False,
    page_loading: bool = False,
    error: str = "",
) -> str:
    """Build tooltip text for the current applet state."""
    if story is None:
        if loading:
            return _("Hacker News: loading...")
        if error:
            return _("Hacker News: {error}").format(error=error)
        return _("Hacker News")

    rank = story_rank(index=index, count=count)
    header = (
        _("{source} {rank}")
        .format(
            source=HN_SOURCE_LABEL,
            rank=rank,
        )
        .strip()
    )
    stats = _("{score} points, {comments} comments").format(
        score=story.score,
        comments=story.comments,
    )
    age = story_age(story=story)
    if age:
        stats = f"{stats}, {age}"
    lines = [header, story.title, stats]
    if page_loading:
        lines.append(_("Loading next stories..."))
    return "\n".join(lines)


def parse_story_payload(data: object) -> HackerNewsStory | None:
    """Parse one HN item payload into a story.

    Deleted, dead, non-story, or untitled items are ignored. Ask/Show HN items
    are normal HN stories, so they pass through as long as the API reports
    ``type == "story"``.
    """
    if not isinstance(data, Mapping):
        return None
    if data.get("deleted") or data.get("dead"):
        return None
    if data.get("type") != "story":
        return None
    try:
        story_id = int(data.get("id", 0))
    except (TypeError, ValueError):
        return None
    title = normalize_text(data.get("title"))
    if story_id <= 0 or not title:
        return None
    try:
        score = int(data.get("score", 0) or 0)
    except (TypeError, ValueError):
        score = 0
    try:
        comments = int(data.get("descendants", 0) or 0)
    except (TypeError, ValueError):
        comments = 0
    try:
        timestamp = int(data.get("time", 0) or 0)
    except (TypeError, ValueError):
        timestamp = 0

    item_url = normalize_text(data.get("url"))
    hn_url = comments_url(story_id)
    return HackerNewsStory(
        id=story_id,
        title=title,
        url=item_url or hn_url,
        hn_url=hn_url,
        score=max(0, score),
        comments=max(0, comments),
        by=normalize_text(data.get("by")),
        time=max(0, timestamp),
    )


def parse_top_story_ids(
    data: object,
    *,
    limit: int,
    offset: int = 0,
) -> tuple[int, ...]:
    """Parse a page from the HN topstories id list.

    Hacker News exposes one ordered ``topstories`` id list rather than a paged
    endpoint. The applet treats ``offset`` as its page cursor into that list.
    """
    if not isinstance(data, Sequence) or isinstance(data, str | bytes):
        return ()
    if limit <= 0:
        return ()
    ids: list[int] = []
    skipped = 0
    offset = max(0, offset)
    for value in data:
        try:
            story_id = int(value)
        except (TypeError, ValueError):
            continue
        if story_id <= 0:
            continue
        if skipped < offset:
            skipped += 1
            continue
        ids.append(story_id)
        if len(ids) >= limit:
            break
    return tuple(ids)


def parse_top_story_id_page(
    data: object,
    *,
    limit: int,
    offset: int = 0,
) -> tuple[tuple[int, ...], int, bool]:
    """Parse one cursor page from HN's ordered topstories id list."""
    if not isinstance(data, Sequence) or isinstance(data, str | bytes):
        return (), max(0, offset), False
    if limit <= 0:
        return (), max(0, offset), False

    valid_ids: list[int] = []
    for value in data:
        try:
            story_id = int(value)
        except (TypeError, ValueError):
            continue
        if story_id > 0:
            valid_ids.append(story_id)

    start = min(max(0, offset), len(valid_ids))
    page_ids = tuple(valid_ids[start : start + limit])
    next_offset = start + len(page_ids)
    return page_ids, next_offset, next_offset < len(valid_ids)


def append_unique_stories(
    *,
    existing: Sequence[HackerNewsStory],
    additions: Sequence[HackerNewsStory],
) -> tuple[HackerNewsStory, ...]:
    """Append new stories without duplicating HN item ids."""
    merged = list(existing)
    seen = {story.id for story in merged}
    for story in additions:
        if story.id in seen:
            continue
        merged.append(story)
        seen.add(story.id)
    return tuple(merged)


def story_to_pref(story: HackerNewsStory) -> dict[str, object]:
    """Serialize one story for applet prefs."""
    return {
        "id": story.id,
        "title": story.title,
        "url": story.url,
        "hn_url": story.hn_url,
        "score": story.score,
        "comments": story.comments,
        "by": story.by,
        "time": story.time,
        "source": story.source,
    }


def story_from_pref(data: object) -> HackerNewsStory | None:
    """Parse one persisted story."""
    if not isinstance(data, Mapping):
        return None
    try:
        story_id = int(data.get("id", 0))
        score = int(data.get("score", 0) or 0)
        comments = int(data.get("comments", 0) or 0)
        timestamp = int(data.get("time", 0) or 0)
    except (TypeError, ValueError):
        return None
    title = normalize_text(data.get("title"))
    if story_id <= 0 or not title:
        return None
    hn_url = normalize_text(data.get("hn_url")) or comments_url(story_id)
    return HackerNewsStory(
        id=story_id,
        title=title,
        url=normalize_text(data.get("url")) or hn_url,
        hn_url=hn_url,
        score=max(0, score),
        comments=max(0, comments),
        by=normalize_text(data.get("by")),
        time=max(0, timestamp),
        source=normalize_text(data.get("source")) or HN_SOURCE,
    )


def prefs_from_mapping(prefs: Mapping[str, Any] | None) -> HackerNewsPrefs:
    """Load persisted Hacker News prefs."""
    if not prefs:
        return HackerNewsPrefs()
    if "next_offset" not in prefs or "has_more_stories" not in prefs:
        return HackerNewsPrefs()
    raw_stories = prefs.get("stories")
    stories: list[HackerNewsStory] = []
    if isinstance(raw_stories, Sequence) and not isinstance(
        raw_stories,
        str | bytes,
    ):
        for item in raw_stories:
            story = story_from_pref(item)
            if story is not None:
                stories.append(story)
            if len(stories) >= MAX_STORIES_IN_PREFS:
                break
    try:
        active_index = int(prefs.get("active_index", 0))
    except (TypeError, ValueError):
        active_index = 0
    return HackerNewsPrefs(
        stories=tuple(stories),
        active_index=normalize_active_index(index=active_index, count=len(stories)),
        next_offset=max(0, _int_from_pref(prefs.get("next_offset"), 0)),
        has_more_stories=bool(prefs.get("has_more_stories")),
        fetched_at=normalize_text(prefs.get("fetched_at")),
    )


def _int_from_pref(value: object, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def prefs_payload(
    *,
    stories: Sequence[HackerNewsStory],
    active_index: int,
    next_offset: int,
    has_more_stories: bool,
    fetched_at: dt.datetime | None = None,
) -> dict[str, object]:
    """Build JSON-safe prefs payload."""
    kept = tuple(stories[:MAX_STORIES_IN_PREFS])
    timestamp = fetched_at or dt.datetime.now(dt.timezone.utc)
    return {
        "source": HN_SOURCE,
        "active_index": normalize_active_index(index=active_index, count=len(kept)),
        "next_offset": max(0, next_offset),
        "has_more_stories": has_more_stories,
        "fetched_at": timestamp.astimezone(dt.timezone.utc).isoformat(),
        "stories": [story_to_pref(story) for story in kept],
    }


def _get_json(url: str, *, timeout: float = FETCH_TIMEOUT_S) -> object:
    request = urllib.request.Request(
        url=url,
        headers={
            "Accept": "application/json",
            "User-Agent": "DockingHackerNewsApplet/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def fetch_hn_stories(
    *,
    limit: int = DEFAULT_FETCH_LIMIT,
    offset: int = 0,
    get_json: Callable[[str], object] | None = None,
) -> tuple[HackerNewsStory, ...]:
    """Fetch HN top stories using the official Firebase API."""
    return fetch_hn_story_page(
        limit=limit,
        offset=offset,
        get_json=get_json,
    ).stories


def fetch_hn_story_page(
    *,
    limit: int = DEFAULT_FETCH_LIMIT,
    offset: int = 0,
    get_json: Callable[[str], object] | None = None,
) -> HackerNewsPage:
    """Fetch one cursor page using HN topstories ids.

    ``has_more`` is based on the id cursor, not on the number of displayable
    stories. That lets paging continue even when a fetched id is dead/deleted
    or otherwise filtered out.
    """
    fetch = get_json or _get_json
    try:
        raw_ids = fetch(f"{HN_BASE_URL}/topstories.json")
        ids, next_offset, has_more = parse_top_story_id_page(
            raw_ids,
            limit=limit,
            offset=offset,
        )
        stories: list[HackerNewsStory] = []
        for story_id in ids:
            raw_story = fetch(f"{HN_BASE_URL}/item/{story_id}.json")
            story = parse_story_payload(raw_story)
            if story is not None:
                stories.append(story)
            if len(stories) >= limit:
                break
        return HackerNewsPage(
            stories=tuple(stories),
            next_offset=next_offset,
            has_more=has_more,
        )
    except Exception as exc:
        log.warning("Failed to fetch Hacker News stories: %s", exc)
        return HackerNewsPage(stories=(), next_offset=max(0, offset), has_more=False)
