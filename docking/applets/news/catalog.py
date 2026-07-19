"""Remote country/news-source catalog validation and XDG cache handling."""

from __future__ import annotations

import datetime as dt
import json
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from docking.applets.http import http_get_bytes
from docking.applets.news.countries import sorted_country_codes
from docking.applets.news.state import (
    NewsSource,
    normalize_country_code,
    normalize_http_url,
)
from docking.applets.text import normalize_text
from docking.core.paths import ensure_parent_dir
from docking.platform.environment import docking_cache_dir

CATALOG_URL = (
    "https://raw.githubusercontent.com/yavuz/news-feed-list-of-countries/"
    "master/active-feeds-auto-generated.json"
)
CATALOG_USER_AGENT = "DockingNewsCatalog/1.0 (+https://github.com/edumucelli/docking)"
CATALOG_CACHE_FILE = docking_cache_dir() / "news" / "catalog.json"
CATALOG_TTL = dt.timedelta(days=7)
CATALOG_TIMEOUT_S = 10
MAX_CATALOG_BYTES = 1024 * 1024
MAX_COUNTRIES = 300
MAX_PUBLICATIONS = 5_000
MAX_FEEDS = 10_000


@dataclass(frozen=True, slots=True)
class NewsCatalog:
    """Validated sources grouped by upstream country code."""

    sources_by_country: Mapping[str, tuple[NewsSource, ...]]

    @property
    def country_codes(self) -> tuple[str, ...]:
        return sorted_country_codes(set(self.sources_by_country))

    @property
    def source_count(self) -> int:
        return sum(len(sources) for sources in self.sources_by_country.values())


@dataclass(frozen=True, slots=True)
class CachedNewsCatalog:
    """A parsed cache plus its filesystem freshness state."""

    catalog: NewsCatalog
    updated_at: dt.datetime
    stale: bool


def parse_catalog(payload: bytes | str) -> NewsCatalog:
    """Validate the generated upstream JSON and flatten publications to feeds."""
    try:
        raw = json.loads(payload)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid news source catalog JSON") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("News source catalog must be a JSON object")
    if len(raw) > MAX_COUNTRIES:
        raise ValueError("News source catalog has too many countries")

    grouped: dict[str, tuple[NewsSource, ...]] = {}
    seen_feed_urls: set[str] = set()
    publication_count = 0
    feed_count = 0
    for raw_country_code, raw_publications in raw.items():
        country_code = normalize_country_code(raw_country_code)
        if not country_code:
            continue
        if not isinstance(raw_publications, Sequence) or isinstance(
            raw_publications, str | bytes
        ):
            continue
        country_sources: list[NewsSource] = []
        for raw_publication in raw_publications:
            publication_count += 1
            if publication_count > MAX_PUBLICATIONS:
                raise ValueError("News source catalog has too many publications")
            if not isinstance(raw_publication, Mapping):
                continue
            publication = cast(Mapping[str, Any], raw_publication)
            publication_name = normalize_text(publication.get("publication_name"))[:300]
            publication_url = normalize_http_url(
                publication.get("publication_website_uri")
            )
            raw_feeds = publication.get("publication_rss_feed_uris")
            if (
                not publication_name
                or not isinstance(raw_feeds, Sequence)
                or isinstance(raw_feeds, str | bytes)
            ):
                continue
            for raw_feed in raw_feeds:
                feed_count += 1
                if feed_count > MAX_FEEDS:
                    raise ValueError("News source catalog has too many feeds")
                if not isinstance(raw_feed, Mapping):
                    continue
                feed = cast(Mapping[str, Any], raw_feed)
                feed_url = normalize_http_url(feed.get("uri"))
                if not feed_url or feed_url in seen_feed_urls:
                    continue
                country_sources.append(
                    NewsSource(
                        country_code=country_code,
                        publication_name=publication_name,
                        publication_url=publication_url,
                        feed_url=feed_url,
                        category=normalize_text(feed.get("category"))[:200],
                        language_code=normalize_text(feed.get("language_code"))[:16],
                        language_name=normalize_text(feed.get("language_name"))[:100],
                    )
                )
                seen_feed_urls.add(feed_url)
        if country_sources:
            grouped[country_code] = tuple(country_sources)
    if not grouped:
        raise ValueError("News source catalog contains no usable feeds")
    return NewsCatalog(sources_by_country=grouped)


def fetch_catalog_payload() -> bytes:
    """Download the bounded generated catalog over HTTPS."""
    return http_get_bytes(
        CATALOG_URL,
        timeout=CATALOG_TIMEOUT_S,
        user_agent=CATALOG_USER_AGENT,
        max_bytes=MAX_CATALOG_BYTES,
    )


def refresh_catalog(*, cache_path: Path = CATALOG_CACHE_FILE) -> CachedNewsCatalog:
    """Fetch, validate, atomically cache, and return a fresh catalog."""
    payload = fetch_catalog_payload()
    catalog = parse_catalog(payload)
    _write_cache(payload=payload, path=cache_path)
    updated_at = dt.datetime.fromtimestamp(
        cache_path.stat().st_mtime,
        tz=dt.timezone.utc,
    )
    return CachedNewsCatalog(
        catalog=catalog,
        updated_at=updated_at,
        stale=False,
    )


def load_cached_catalog(
    *,
    cache_path: Path = CATALOG_CACHE_FILE,
    now: dt.datetime | None = None,
) -> CachedNewsCatalog | None:
    """Load the last valid cache, returning None for missing/corrupt data."""
    try:
        payload = cache_path.read_bytes()
        if len(payload) > MAX_CATALOG_BYTES:
            return None
        catalog = parse_catalog(payload)
        updated_at = dt.datetime.fromtimestamp(
            cache_path.stat().st_mtime,
            tz=dt.timezone.utc,
        )
    except (OSError, ValueError):
        return None
    current = now or dt.datetime.now(dt.timezone.utc)
    return CachedNewsCatalog(
        catalog=catalog,
        updated_at=updated_at,
        stale=current - updated_at >= CATALOG_TTL,
    )


def _write_cache(*, payload: bytes, path: Path) -> None:
    ensure_parent_dir(path)
    with tempfile.NamedTemporaryFile(
        "wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        tmp_path = Path(handle.name)
        handle.write(payload)
    try:
        tmp_path.replace(path)
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise
