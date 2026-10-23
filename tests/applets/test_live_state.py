import datetime as dt

from docking.applets.live_state import (
    LiveDataStatus,
    is_stale,
    live_freshness_lines,
    live_state_error,
    live_state_label,
    refresh_recovery_label,
    resolve_live_status,
    stale_label,
)


def test_resolve_live_status_distinguishes_empty_loading_and_error():
    assert resolve_live_status(has_data=False) is LiveDataStatus.EMPTY
    assert resolve_live_status(has_data=False, loading=True) is LiveDataStatus.LOADING
    assert resolve_live_status(has_data=False, error="down") is LiveDataStatus.ERROR


def test_resolve_live_status_keeps_old_data_as_stale_on_error():
    assert resolve_live_status(has_data=True, error="down") is LiveDataStatus.STALE


def test_resolve_live_status_uses_age_when_configured():
    updated = dt.datetime(2026, 4, 30, 10, 0, tzinfo=dt.timezone.utc)
    now = dt.datetime(2026, 4, 30, 10, 10, tzinfo=dt.timezone.utc)

    assert is_stale(updated, max_age_seconds=60, now=now)
    assert (
        resolve_live_status(
            has_data=True,
            updated_at=updated,
            stale_after_seconds=60,
            now=now,
        )
        is LiveDataStatus.STALE
    )


def test_live_state_label_and_error_detail():
    assert live_state_label(LiveDataStatus.EMPTY) == "No data yet"
    assert live_state_label(LiveDataStatus.LOADING) == "Loading..."
    assert live_state_label(LiveDataStatus.READY) == ""
    assert live_state_label(LiveDataStatus.STALE) == "Stale data"
    assert live_state_label(LiveDataStatus.ERROR) == "Unavailable"
    assert (
        live_state_error(status=LiveDataStatus.STALE, error="network down")
        == "network down"
    )
    assert live_state_error(status=LiveDataStatus.READY, error="network down") is None


def test_live_freshness_uses_stale_label_before_cadence():
    updated = dt.datetime(2026, 4, 30, 10, 0, tzinfo=dt.timezone.utc)
    now = dt.datetime(2026, 4, 30, 10, 10, tzinfo=dt.timezone.utc)

    lines = live_freshness_lines(
        status=LiveDataStatus.STALE,
        updated_at=updated,
        cadence_seconds=300,
        now=now,
    )

    assert lines[0] == "Stale: last updated 10 minutes ago"
    assert lines[1] == "Updates every 5 minutes"
    assert stale_label(None) == "Stale data"


def test_live_freshness_uses_relative_updated_label_before_cadence():
    updated = dt.datetime(2026, 4, 30, 10, 0, tzinfo=dt.timezone.utc)
    now = dt.datetime(2026, 4, 30, 10, 2, tzinfo=dt.timezone.utc)

    lines = live_freshness_lines(
        status=LiveDataStatus.READY,
        updated_at=updated,
        cadence_seconds=300,
        now=now,
    )

    assert lines[0] == "Updated: 2 minutes ago"
    assert lines[1] == "Updates every 5 minutes"


def test_refresh_recovery_label_only_for_non_ready_states():
    assert (
        refresh_recovery_label(LiveDataStatus.ERROR) == "Use Refresh Now from the menu."
    )
    assert refresh_recovery_label(LiveDataStatus.READY) is None
