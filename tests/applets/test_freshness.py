import datetime as dt

from docking.applets.freshness import (
    cadence_label,
    format_interval,
    on_demand_label,
    relative_time_label,
    updated_label,
)

NOW = dt.datetime(2026, 4, 27, 12, 0, tzinfo=dt.timezone.utc)


def test_format_interval_prefers_largest_clean_unit():
    assert format_interval(5) == "5 seconds"
    assert format_interval(300) == "5 minutes"
    assert format_interval(3600) == "1 hour"


def test_cadence_label_uses_default_and_custom_verbs():
    assert cadence_label(seconds=300) == "Updates every 5 minutes"
    assert cadence_label(seconds=5, verb="Samples") == "Samples every 5 seconds"


def test_on_demand_label():
    assert on_demand_label(verb="Runs") == "Runs on demand"


def test_relative_time_label_formats_recent_timestamps():
    assert relative_time_label(NOW, now=NOW) == "just now"
    assert relative_time_label(NOW - dt.timedelta(seconds=1), now=NOW) == "1 second ago"
    assert (
        relative_time_label(NOW - dt.timedelta(seconds=42), now=NOW) == "42 seconds ago"
    )
    assert relative_time_label(NOW - dt.timedelta(minutes=1), now=NOW) == "1 minute ago"
    assert (
        relative_time_label(NOW - dt.timedelta(minutes=5), now=NOW) == "5 minutes ago"
    )
    assert relative_time_label(NOW - dt.timedelta(hours=1), now=NOW) == "1 hour ago"
    assert relative_time_label(NOW - dt.timedelta(hours=2), now=NOW) == "2 hours ago"
    assert relative_time_label(NOW - dt.timedelta(days=1), now=NOW) == "1 day ago"
    assert relative_time_label(NOW - dt.timedelta(days=3), now=NOW) == "3 days ago"


def test_relative_time_label_clamps_future_timestamps():
    assert relative_time_label(NOW + dt.timedelta(seconds=30), now=NOW) == "just now"


def test_updated_label_formats_relative_timestamp():
    timestamp = NOW - dt.timedelta(minutes=5)

    assert updated_label(timestamp, now=NOW) == "Updated: 5 minutes ago"
    assert updated_label("") == ""
