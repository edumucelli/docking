"""State and data helpers for the Today in History applet."""

from __future__ import annotations

import html
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any
from urllib.request import Request, urlopen

from docking.applets.todayinhistory import meta
from docking.i18n import _
from docking.log import get_logger, with_context

_log = with_context(
    get_logger(name="todayinhistory"),
    applet_id=meta.id,
)
_EVENTS_RESOURCE = "history/todayinhistory.json"
_WIKIPEDIA_ENDPOINT = (
    "https://en.wikipedia.org/api/rest_v1/feed/onthisday/events/{month}/{day}"
)
_WIKIPEDIA_SOURCE = "Wikipedia"
_OFFLINE_SOURCE = _("Offline fallback")


@dataclass(frozen=True, slots=True)
class HistoryEvent:
    year: int
    title: str
    summary: str
    article_title: str = ""
    article_url: str = ""
    source_label: str = _WIKIPEDIA_SOURCE


def normalize_text(text: str) -> str:
    clean = html.unescape(text).replace("\n", " ").replace("\r", " ").strip()
    return " ".join(clean.split())


def format_history_event(event: HistoryEvent) -> str:
    header = _("{year} - {title}").format(year=event.year, title=event.title)
    if event.summary:
        return "\n".join((header, event.summary))
    return header


def _http_get_json(url: str, timeout: float = 8.0) -> Any:
    request = Request(
        url=url,
        headers={
            "User-Agent": (
                "DockingTodayInHistoryApplet/1.0 "
                "(+https://github.com/edumucelli/docking)"
            )
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8", errors="replace")
    return json.loads(payload)


def _date_key(*, month: int, day: int) -> str:
    return f"{max(1, month):02d}-{max(1, day):02d}"


def _coerce_year(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _extract_article(page: object) -> tuple[str, str]:
    if not isinstance(page, dict):
        return "", ""
    raw_page = {str(key): value for key, value in page.items()}

    title = ""
    titles = raw_page.get("titles")
    if isinstance(titles, dict):
        raw_titles = {str(key): value for key, value in titles.items()}
        raw_normalized = raw_titles.get("normalized")
        if isinstance(raw_normalized, str):
            title = normalize_text(raw_normalized)
    if not title:
        raw_title = raw_page.get("title") or raw_page.get("normalizedtitle")
        if isinstance(raw_title, str):
            title = normalize_text(raw_title)

    url = ""
    raw_content_urls = raw_page.get("content_urls")
    if isinstance(raw_content_urls, dict):
        content_urls = {str(key): value for key, value in raw_content_urls.items()}
        for variant in ("desktop", "mobile"):
            raw_variant = content_urls.get(variant)
            if not isinstance(raw_variant, dict):
                continue
            variant_map = {str(key): value for key, value in raw_variant.items()}
            raw_page = variant_map.get("page")
            if isinstance(raw_page, str):
                url = raw_page.strip()
                if url:
                    break

    return title, url


def _event_from_mapping(
    raw: Mapping[str, object],
    *,
    default_source: str,
) -> HistoryEvent | None:
    year = _coerce_year(raw.get("year"))
    raw_title = raw.get("title")
    raw_summary = raw.get("summary")
    raw_article_title = raw.get("article_title", "")
    raw_article_url = raw.get("article_url", "")
    raw_source = raw.get("source_label", default_source)
    if (
        year is None
        or not isinstance(raw_title, str)
        or not isinstance(raw_summary, str)
    ):
        return None

    title = normalize_text(raw_title)
    summary = normalize_text(raw_summary)
    article_title = (
        normalize_text(raw_article_title) if isinstance(raw_article_title, str) else ""
    )
    article_url = raw_article_url.strip() if isinstance(raw_article_url, str) else ""
    source_label = (
        normalize_text(raw_source) if isinstance(raw_source, str) else default_source
    )

    if not title or not summary:
        return None

    return HistoryEvent(
        year=year,
        title=title,
        summary=summary,
        article_title=article_title,
        article_url=article_url,
        source_label=source_label or default_source,
    )


def _parse_wikipedia_events(data: Any, limit: int) -> list[HistoryEvent]:
    if not isinstance(data, dict):
        return []
    raw_events = data.get("events")
    if not isinstance(raw_events, list):
        return []

    events: list[HistoryEvent] = []
    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            continue

        year = _coerce_year(raw_event.get("year"))
        raw_summary = raw_event.get("text")
        if year is None or not isinstance(raw_summary, str):
            continue

        summary = normalize_text(raw_summary)
        if not summary:
            continue

        title = ""
        article_title = ""
        article_url = ""
        raw_pages = raw_event.get("pages")
        if isinstance(raw_pages, list):
            for raw_page in raw_pages:
                article_title, article_url = _extract_article(raw_page)
                if article_title or article_url:
                    title = article_title or title
                    break

        if not title:
            title = summary.split(".", 1)[0].strip()
        if not title:
            continue

        events.append(
            HistoryEvent(
                year=year,
                title=title,
                summary=summary,
                article_title=article_title or title,
                article_url=article_url,
                source_label=_WIKIPEDIA_SOURCE,
            )
        )
        if len(events) >= limit:
            break

    return events


def fetch_today_in_history(
    month: int,
    day: int,
    limit: int = 20,
    http_get_json: Callable[[str], Any] | None = None,
) -> list[HistoryEvent]:
    getter = http_get_json or _http_get_json
    try:
        data = getter(_WIKIPEDIA_ENDPOINT.format(month=month, day=day))
        return _parse_wikipedia_events(data=data, limit=limit)
    except Exception as exc:
        _log.bind(action="fetch_today_in_history").debug(
            "Failed to fetch history events for %s: %s",
            _date_key(month=month, day=day),
            exc,
        )
        return []


def _load_fallback_catalog() -> dict[str, list[HistoryEvent]]:
    try:
        events_ref = resources.files("docking.assets").joinpath(_EVENTS_RESOURCE)
        with (
            resources.as_file(events_ref) as path,
            path.open(encoding="utf-8") as handle,
        ):
            payload = json.load(handle)
    except (
        FileNotFoundError,
        ModuleNotFoundError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        _log.warning("Failed to load today-in-history fallback asset: %s", exc)
        return {}

    if not isinstance(payload, dict):
        return {}

    catalog: dict[str, list[HistoryEvent]] = {}
    for raw_key, raw_entries in payload.items():
        if not isinstance(raw_key, str) or not isinstance(raw_entries, list):
            continue
        entries = [
            entry
            for raw_entry in raw_entries
            if isinstance(raw_entry, Mapping)
            and (
                entry := _event_from_mapping(
                    raw_entry,
                    default_source=_OFFLINE_SOURCE,
                )
            )
            is not None
        ]
        if entries:
            catalog[raw_key] = entries

    return catalog


def fallback_today_in_history(
    month: int,
    day: int,
    catalog: Mapping[str, Sequence[HistoryEvent]] | None = None,
) -> list[HistoryEvent]:
    entries_by_day = catalog or _load_fallback_catalog()
    exact = entries_by_day.get(_date_key(month=month, day=day))
    if exact:
        return list(exact)

    pool: list[HistoryEvent] = []
    for key in sorted(entries_by_day):
        pool.extend(entries_by_day[key])
    if not pool:
        return []

    start = (((month - 1) * 31) + (day - 1)) % len(pool)
    count = min(3, len(pool))
    return [pool[(start + offset) % len(pool)] for offset in range(count)]
