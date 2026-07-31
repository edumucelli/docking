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


def test_current_time_and_timezone_conversion_are_detected() -> None:
    tokyo = parse_temporal_query("time in Tokyo", now=NOW)
    new_york = parse_temporal_query("10:00 UTC to New York", now=NOW)

    assert tokyo is not None
    assert tokyo.kind is TemporalKind.CURRENT_TIME
    assert tokyo.title == "21:00"
    assert new_york is not None
    assert new_york.kind is TemporalKind.TIME_CONVERSION
    assert new_york.title == "06:00 · America/New_York"


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
