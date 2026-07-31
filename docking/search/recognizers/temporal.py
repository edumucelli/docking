"""Recognize and evaluate date and time-zone queries."""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from docking.i18n import _


class TemporalKind(str, Enum):
    DATE = "date"
    CURRENT_TIME = "current-time"
    TIME_CONVERSION = "time-conversion"


@dataclass(frozen=True, slots=True)
class TemporalValue:
    kind: TemporalKind
    title: str
    description: str
    copy_text: str
    state: str
    canonical_key: str


def _normalize_timezone_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(without_accents.replace("_", " ").split()).casefold()


def _build_timezone_indexes(
    zones: Iterable[str],
) -> tuple[dict[str, str], dict[str, str]]:
    qualified: dict[str, str] = {}
    short_candidates: defaultdict[str, set[str]] = defaultdict(set)
    for zone_name in sorted(set(zones)):
        qualified[_normalize_timezone_name(zone_name)] = zone_name
        qualified[_normalize_timezone_name(zone_name.replace("/", " "))] = zone_name
        short_name = zone_name.rsplit("/", 1)[-1]
        short_candidates[_normalize_timezone_name(short_name)].add(zone_name)
    unique_short_names = {
        alias: next(iter(candidates))
        for alias, candidates in short_candidates.items()
        if len(candidates) == 1
    }
    return qualified, unique_short_names


_QUALIFIED_TIMEZONES, _UNIQUE_TIMEZONE_ALIASES = _build_timezone_indexes(
    available_timezones()
)
_CURRENT_TIME_RE = re.compile(r"^(?:time|now)\s+in\s+(.+)$", re.IGNORECASE)
_CITY_TIME_RE = re.compile(r"^(.+?)\s+time$", re.IGNORECASE)
_TIME_CONVERSION_RE = re.compile(
    r"^(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*"
    r"(?P<ampm>am|pm)?\s+"
    r"(?P<source>.+?)\s+to\s+(?P<target>.+)$",
    re.IGNORECASE,
)


def _resolve_timezone(value: str) -> tuple[str, ZoneInfo] | None:
    normalized = _normalize_timezone_name(value.strip())
    zone_name = _QUALIFIED_TIMEZONES.get(normalized) or _UNIQUE_TIMEZONE_ALIASES.get(
        normalized
    )
    if zone_name is None:
        return None
    try:
        return zone_name, ZoneInfo(zone_name)
    except ZoneInfoNotFoundError:
        return None


def _formatted_date(value: dt.date) -> str:
    return value.strftime("%A, %x")


def _relative_date(value: dt.date, today: dt.date) -> str:
    difference = (value - today).days
    if difference == 0:
        return _("Today")
    if difference == 1:
        return _("Tomorrow")
    if difference == -1:
        return _("Yesterday")
    if difference > 1:
        return _("In {count} days").format(count=difference)
    return _("{count} days ago").format(count=abs(difference))


def _date_value(
    value: dt.date,
    *,
    today: dt.date,
) -> TemporalValue:
    iso = value.isoformat()
    return TemporalValue(
        kind=TemporalKind.DATE,
        title=_formatted_date(value),
        description=_relative_date(value, today),
        copy_text=iso,
        state=_("Date"),
        canonical_key=f"date:{iso}",
    )


def _parse_date(text: str, *, today: dt.date) -> TemporalValue | None:
    keyword_dates = {
        "today": today,
        "tomorrow": today + dt.timedelta(days=1),
        "yesterday": today - dt.timedelta(days=1),
    }
    normalized = text.casefold()
    if normalized in keyword_dates:
        return _date_value(keyword_dates[normalized], today=today)
    try:
        value = dt.date.fromisoformat(text)
    except ValueError:
        return None
    return _date_value(value, today=today)


def _time_description(value: dt.datetime, zone_name: str) -> str:
    return f"{_formatted_date(value.date())} · {zone_name}"


def _current_time_value(
    zone_name: str,
    zone: ZoneInfo,
    *,
    now: dt.datetime,
) -> TemporalValue:
    value = now.astimezone(zone)
    shown = value.strftime("%H:%M")
    return TemporalValue(
        kind=TemporalKind.CURRENT_TIME,
        title=shown,
        description=_time_description(value, zone_name),
        copy_text=shown,
        state=_("Time Zone"),
        canonical_key=f"time:{zone_name}",
    )


def _converted_time_value(
    match: re.Match[str],
    *,
    now: dt.datetime,
) -> TemporalValue | None:
    source = _resolve_timezone(match.group("source"))
    target = _resolve_timezone(match.group("target"))
    if source is None or target is None:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    ampm = (match.group("ampm") or "").casefold()
    if minute > 59 or hour > (12 if ampm else 23) or (hour == 0 and ampm):
        return None
    if ampm:
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    source_name, source_zone = source
    target_name, target_zone = target
    source_time = dt.datetime.combine(
        now.astimezone(source_zone).date(),
        dt.time(hour=hour, minute=minute),
        tzinfo=source_zone,
    )
    converted = source_time.astimezone(target_zone)
    shown = converted.strftime("%H:%M")
    return TemporalValue(
        kind=TemporalKind.TIME_CONVERSION,
        title=f"{shown} · {target_name}",
        description=_("{time} {source} to {target}").format(
            time=source_time.strftime("%H:%M"),
            source=source_name,
            target=target_name,
        ),
        copy_text=shown,
        state=_("Time Zone"),
        canonical_key=(f"time-conversion:{source_time.isoformat()}:{target_name}"),
    )


def parse_temporal_query(
    text: str,
    *,
    now: dt.datetime | None = None,
) -> TemporalValue | None:
    """Recognize ISO/relative dates and common time-zone expressions."""
    stripped = text.strip()
    if not stripped:
        return None
    current = now or dt.datetime.now().astimezone()
    date_value = _parse_date(stripped, today=current.date())
    if date_value is not None:
        return date_value
    current_match = _CURRENT_TIME_RE.fullmatch(stripped) or _CITY_TIME_RE.fullmatch(
        stripped
    )
    if current_match is not None:
        zone = _resolve_timezone(current_match.group(1))
        if zone is not None:
            return _current_time_value(*zone, now=current)
    conversion_match = _TIME_CONVERSION_RE.fullmatch(stripped)
    if conversion_match is not None:
        return _converted_time_value(conversion_match, now=current)
    return None


__all__ = [
    "TemporalKind",
    "TemporalValue",
    "parse_temporal_query",
]
