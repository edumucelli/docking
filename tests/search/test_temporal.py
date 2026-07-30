"""Tests for date and time-zone query utilities."""

from __future__ import annotations

import datetime as dt

from docking.search.recognizers.temporal import TemporalKind, parse_temporal_query

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


def test_unknown_timezones_and_invalid_dates_are_not_claimed() -> None:
    assert parse_temporal_query("time in Atlantis", now=NOW) is None
    assert parse_temporal_query("2026-02-31", now=NOW) is None
