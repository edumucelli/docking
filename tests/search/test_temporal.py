"""Tests for date and time-zone query utilities."""

from __future__ import annotations

import datetime as dt

from docking.search.recognizers.temporal import (
    TemporalKind,
    _build_timezone_indexes,
    parse_temporal_query,
)

NOW = dt.datetime(2026, 7, 28, 12, 0, tzinfo=dt.timezone.utc)


def test_iso_and_relative_dates_are_detected() -> None:
    iso = parse_temporal_query("2026-07-28", now=NOW)
    tomorrow = parse_temporal_query("tomorrow", now=NOW)

    assert iso is not None
    assert iso.kind is TemporalKind.DATE
    assert iso.title == NOW.date().strftime("%A, %x")
    assert iso.description == "Today"
    assert tomorrow is not None
    assert tomorrow.copy_text == "2026-07-29"


def test_bare_relative_weekday_and_slash_dates_are_detected() -> None:
    date = parse_temporal_query("date", now=NOW)
    slash_date = parse_temporal_query("2026/08/01", now=NOW)
    future = parse_temporal_query("in 3 days", now=NOW)
    past = parse_temporal_query("2 weeks ago", now=NOW)
    next_monday = parse_temporal_query("next Monday", now=NOW)

    assert date is not None and date.copy_text == "2026-07-28"
    assert slash_date is not None and slash_date.copy_text == "2026-08-01"
    assert future is not None and future.copy_text == "2026-07-31"
    assert past is not None and past.copy_text == "2026-07-14"
    assert next_monday is not None and next_monday.copy_text == "2026-08-03"


def test_current_time_and_timezone_conversion_are_detected() -> None:
    local = parse_temporal_query("time", now=NOW)
    tokyo = parse_temporal_query("time in Tokyo", now=NOW)
    new_york = parse_temporal_query("10:00 UTC to New York", now=NOW)

    assert local is not None
    assert local.title == "12:00"
    assert tokyo is not None
    assert tokyo.kind is TemporalKind.CURRENT_TIME
    assert tokyo.title == "21:00"
    assert new_york is not None
    assert new_york.kind is TemporalKind.TIME_CONVERSION
    assert new_york.title == "06:00 · America/New_York"


def test_utc_offsets_and_dated_timezone_conversions_are_detected() -> None:
    offset = parse_temporal_query("time in UTC+05:30", now=NOW)
    dated = parse_temporal_query(
        "2026-12-01 23:30 Paris to Tokyo",
        now=NOW,
    )

    assert offset is not None
    assert offset.title == "17:30"
    assert offset.canonical_key == "time:UTC+05:30"
    assert dated is not None
    assert dated.title == "2026-12-02 07:30 · Asia/Tokyo"


def test_timezone_aliases_are_generated_from_available_zones() -> None:
    kathmandu = parse_temporal_query("time in Kathmandu", now=NOW)
    sao_paulo = parse_temporal_query("time in São Paulo", now=NOW)
    qualified = parse_temporal_query("time in america new york", now=NOW)

    assert kathmandu is not None
    assert kathmandu.canonical_key == "time:Asia/Kathmandu"
    assert sao_paulo is not None
    assert sao_paulo.canonical_key == "time:America/Sao_Paulo"
    assert qualified is not None
    assert qualified.canonical_key == "time:America/New_York"


def test_ambiguous_generated_aliases_require_a_qualified_name() -> None:
    qualified, aliases = _build_timezone_indexes(
        ("Area/Shared_City", "Other/Shared_City")
    )

    assert "shared city" not in aliases
    assert qualified["area/shared city"] == "Area/Shared_City"
    assert qualified["area shared city"] == "Area/Shared_City"


def test_unknown_timezones_and_invalid_dates_are_not_claimed() -> None:
    assert parse_temporal_query("time in Atlantis", now=NOW) is None
    assert parse_temporal_query("time in NYC", now=NOW) is None
    assert parse_temporal_query("2026-02-31", now=NOW) is None
