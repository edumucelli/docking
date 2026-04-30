import datetime as dt

from docking.applets.freshness import (
    cadence_label,
    format_interval,
    on_demand_label,
    updated_label,
)


def test_format_interval_prefers_largest_clean_unit():
    assert format_interval(5) == "5 seconds"
    assert format_interval(300) == "5 minutes"
    assert format_interval(3600) == "1 hour"


def test_cadence_label_uses_default_and_custom_verbs():
    assert cadence_label(seconds=300) == "Updates every 5 minutes"
    assert cadence_label(seconds=5, verb="Samples") == "Samples every 5 seconds"


def test_on_demand_label():
    assert on_demand_label(verb="Runs") == "Runs on demand"


def test_updated_label_formats_valid_timestamp():
    timestamp = dt.datetime(2026, 4, 27, 12, 0, tzinfo=dt.timezone.utc)

    assert "Updated:" in updated_label(timestamp)
    assert "2026-04-27" in updated_label(timestamp)
    assert updated_label("") == ""
