"""Tests for the desk-presence applet."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from docking.applets.deskpresence.applet import DeskpresenceApplet
from docking.applets.deskpresence.state import (
    DEFAULT_IDLE_THRESHOLD_S,
    MAX_HISTORY_DAYS,
    DayEntry,
    DeskpresencePrefs,
    Presence,
    PresenceState,
    apply_tick,
    build_tooltip,
    format_badge,
    format_duration,
    prefs_from_mapping,
    prefs_payload,
    state_from_prefs,
    week_at_desk_seconds,
    week_away_seconds,
)
from docking.core.config import Config


def _make_applet(
    icon_size: int = 48, *, config: Config | None = None
) -> DeskpresenceApplet:
    return DeskpresenceApplet(icon_size, config=config)


class TestFormatDuration:
    def test_minutes_only(self):
        assert format_duration(120.0) == "2m"

    def test_hours_and_minutes(self):
        assert format_duration(3 * 3600 + 24 * 60) == "3h 24m"

    def test_zero(self):
        assert format_duration(0.0) == "0m"

    def test_negative_clamped(self):
        assert format_duration(-100.0) == "0m"


class TestFormatBadge:
    def test_hides_below_one_minute(self):
        assert format_badge(30.0) == ""

    def test_minute_badge(self):
        assert format_badge(5 * 60) == "5m"

    def test_hour_badge(self):
        assert format_badge(2 * 3600 + 30 * 60) == "2h"


class TestApplyTickCredits:
    def _base_state(self) -> PresenceState:
        return PresenceState(
            today=date(2026, 4, 24),
            session_start_epoch=1000.0,
            presence=Presence.AT_DESK,
            idle_threshold_s=DEFAULT_IDLE_THRESHOLD_S,
        )

    def test_credits_at_desk_time(self):
        state = self._base_state()
        apply_tick(
            state=state,
            idle_ms=500,
            now_epoch=1010.0,
            today=date(2026, 4, 24),
        )
        assert state.at_desk_seconds == 10.0
        assert state.away_seconds == 0.0
        assert state.presence is Presence.AT_DESK

    def test_credits_away_time(self):
        state = self._base_state()
        state.presence = Presence.AWAY
        apply_tick(
            state=state,
            idle_ms=200_000,
            now_epoch=1030.0,
            today=date(2026, 4, 24),
        )
        assert state.away_seconds == 30.0
        assert state.at_desk_seconds == 0.0

    def test_unknown_idle_does_not_credit(self):
        state = self._base_state()
        apply_tick(
            state=state,
            idle_ms=None,
            now_epoch=1010.0,
            today=date(2026, 4, 24),
        )
        # previous state was AT_DESK so elapsed time still credits,
        # but new presence becomes UNKNOWN.
        assert state.at_desk_seconds == 10.0
        assert state.presence is Presence.UNKNOWN

    def test_state_transition_resets_session(self):
        state = self._base_state()
        apply_tick(
            state=state,
            idle_ms=500_000,
            now_epoch=1050.0,
            today=date(2026, 4, 24),
        )
        assert state.presence is Presence.AWAY
        assert state.session_start_epoch == 1050.0

    def test_day_rollover_archives_and_resets(self):
        state = self._base_state()
        state.at_desk_seconds = 1234.0
        state.away_seconds = 5678.0
        apply_tick(
            state=state,
            idle_ms=500,
            now_epoch=1100.0,
            today=date(2026, 4, 25),
        )
        assert state.today == date(2026, 4, 25)
        assert state.at_desk_seconds == 0.0
        assert state.away_seconds == 0.0
        assert len(state.history) == 1
        entry = state.history[0]
        assert entry.date == "2026-04-24"
        assert entry.at_desk_seconds == 1234.0
        assert entry.away_seconds == 5678.0

    def test_rollover_without_activity_is_not_archived(self):
        state = self._base_state()
        state.at_desk_seconds = 0.0
        state.away_seconds = 0.0
        apply_tick(
            state=state,
            idle_ms=500,
            now_epoch=1100.0,
            today=date(2026, 4, 25),
        )
        assert state.history == []


class TestPrefsRoundTrip:
    def test_none_returns_defaults(self):
        assert prefs_from_mapping(None) == DeskpresencePrefs()

    def test_round_trips(self):
        state = PresenceState(
            today=date(2026, 4, 24),
            at_desk_seconds=1200.0,
            away_seconds=300.0,
            idle_threshold_s=180.0,
        )
        payload = prefs_payload(state=state)
        back = prefs_from_mapping(payload)
        assert back.today == "2026-04-24"
        assert back.at_desk_seconds == 1200.0
        assert back.away_seconds == 300.0
        assert back.idle_threshold_s == 180.0

    def test_clamps_threshold(self):
        raw = {"idle_threshold_s": 5.0}
        prefs = prefs_from_mapping(raw)
        assert prefs.idle_threshold_s >= 30.0

        raw = {"idle_threshold_s": 99999.0}
        prefs = prefs_from_mapping(raw)
        assert prefs.idle_threshold_s <= 3600.0

    def test_stale_day_clears_totals_on_rehydrate(self):
        prefs = DeskpresencePrefs(
            today="2026-04-23",
            at_desk_seconds=1000.0,
            away_seconds=200.0,
            idle_threshold_s=120.0,
        )
        state = state_from_prefs(prefs=prefs, today=date(2026, 4, 24))
        assert state.at_desk_seconds == 0.0
        assert state.away_seconds == 0.0
        assert state.idle_threshold_s == 120.0

    def test_same_day_restores_totals(self):
        prefs = DeskpresencePrefs(
            today="2026-04-24",
            at_desk_seconds=1000.0,
            away_seconds=200.0,
            idle_threshold_s=120.0,
        )
        state = state_from_prefs(prefs=prefs, today=date(2026, 4, 24))
        assert state.at_desk_seconds == 1000.0
        assert state.away_seconds == 200.0


class TestTooltip:
    def test_includes_status_and_totals(self):
        state = PresenceState(
            today=date(2026, 4, 24),
            at_desk_seconds=3600.0,
            away_seconds=600.0,
            presence=Presence.AT_DESK,
            session_start_epoch=1000.0,
        )
        text = build_tooltip(state=state, now_epoch=1300.0)
        assert "At desk" in text
        assert "1h 0m" in text
        assert "10m" in text
        assert "Session" in text

    def test_tooltip_lists_last_7_days_when_history_present(self):
        state = PresenceState(
            today=date(2026, 4, 24),
            at_desk_seconds=3600.0,
            away_seconds=0.0,
            session_start_epoch=0.0,
            history=[
                DayEntry(date="2026-04-23", at_desk_seconds=7200.0, away_seconds=0.0),
                DayEntry(date="2026-04-22", at_desk_seconds=1800.0, away_seconds=0.0),
            ],
        )
        text = build_tooltip(state=state, now_epoch=0.0)
        assert "Last 7 days" in text
        assert "Week total at desk" in text
        # Each past day rendered with a weekday label; today row included too.
        assert "Fri" in text  # 2026-04-24 is a Friday
        assert "Thu" in text  # 2026-04-23
        assert "Wed" in text  # 2026-04-22


class TestWeekTotals:
    def test_week_sums_today_plus_history(self):
        state = PresenceState(
            today=date(2026, 4, 24),
            at_desk_seconds=1000.0,
            away_seconds=500.0,
            history=[
                DayEntry("2026-04-23", at_desk_seconds=2000.0, away_seconds=0.0),
                DayEntry("2026-04-22", at_desk_seconds=500.0, away_seconds=100.0),
            ],
        )
        assert week_at_desk_seconds(state) == 3500.0
        assert week_away_seconds(state) == 600.0


class TestHistoryPrefs:
    def test_round_trips_history(self):
        state = PresenceState(
            today=date(2026, 4, 24),
            at_desk_seconds=0.0,
            away_seconds=0.0,
            history=[
                DayEntry("2026-04-23", at_desk_seconds=3600.0, away_seconds=0.0),
                DayEntry("2026-04-22", at_desk_seconds=1800.0, away_seconds=200.0),
            ],
        )
        payload = prefs_payload(state=state)
        with patch(
            "docking.applets.deskpresence.state._today_utc",
            return_value=state.today,
        ):
            back = prefs_from_mapping(payload)
        assert len(back.history) == 2
        assert back.history[0].date == "2026-04-23"
        assert back.history[0].at_desk_seconds == 3600.0

    def test_history_capped_at_max(self):
        days = [
            {
                "date": f"2026-04-{10 + i:02d}",
                "at_desk_seconds": 60.0,
                "away_seconds": 0.0,
            }
            for i in range(20)
        ]
        with patch(
            "docking.applets.deskpresence.state._today_utc",
            return_value=date(2026, 4, 30),
        ):
            prefs = prefs_from_mapping({"history": days})
        assert len(prefs.history) <= MAX_HISTORY_DAYS

    def test_malformed_history_entries_skipped(self):
        raw = {
            "history": [
                "garbage",
                {"date": "not-a-date", "at_desk_seconds": 0, "away_seconds": 0},
                {"date": "2026-04-23", "at_desk_seconds": 100, "away_seconds": 0},
            ]
        }
        with patch(
            "docking.applets.deskpresence.state._today_utc",
            return_value=date(2026, 4, 24),
        ):
            prefs = prefs_from_mapping(raw)
        assert len(prefs.history) == 1
        assert prefs.history[0].date == "2026-04-23"

    def test_stale_today_is_folded_into_history_on_rehydrate(self):
        prefs = DeskpresencePrefs(
            today="2026-04-23",
            at_desk_seconds=3600.0,
            away_seconds=600.0,
        )
        state = state_from_prefs(prefs=prefs, today=date(2026, 4, 24))
        assert state.at_desk_seconds == 0.0
        assert state.away_seconds == 0.0
        assert len(state.history) == 1
        assert state.history[0].date == "2026-04-23"
        assert state.history[0].at_desk_seconds == 3600.0

    def test_stale_today_beyond_window_is_dropped(self):
        prefs = DeskpresencePrefs(
            today="2025-01-01",
            at_desk_seconds=999.0,
            away_seconds=0.0,
        )
        state = state_from_prefs(prefs=prefs, today=date(2026, 4, 24))
        assert state.at_desk_seconds == 0.0
        assert state.history == []


class TestAppletLifecycle:
    def test_creates_with_default_icon(self):
        applet = _make_applet()
        assert applet.item.icon is not None

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = _make_applet(size)
            assert applet.create_icon(size) is not None

    def test_tooltip_sets_status_unknown_initially(self):
        applet = _make_applet()
        applet.refresh_tooltip()
        assert "Desk Presence" in applet.item.name
        assert "Unknown" in applet.item.name


class TestAppletTick:
    def test_tick_credits_from_probe(self):
        applet = _make_applet()
        applet._idle_probe = lambda: 500  # definitely at-desk
        applet._state.session_start_epoch = 2000.0
        applet._state.presence = Presence.AT_DESK
        with patch(
            "docking.applets.deskpresence.applet.time.time", return_value=2015.0
        ):
            applet._tick()
        assert applet._state.at_desk_seconds == 15.0
        assert applet._state.presence is Presence.AT_DESK

    def test_tick_credits_away_bucket(self):
        applet = _make_applet()
        applet._state.session_start_epoch = 2000.0
        applet._state.presence = Presence.AWAY
        applet._idle_probe = lambda: 10 * 60 * 1000  # 10 minutes idle
        with patch(
            "docking.applets.deskpresence.applet.time.time", return_value=2020.0
        ):
            applet._tick()
        assert applet._state.away_seconds == 20.0

    def test_probe_failure_does_not_crash(self):
        applet = _make_applet()
        applet._idle_probe = lambda: None
        applet._tick()
        # No assertion other than surviving; presence becomes UNKNOWN.
        assert applet._state.presence is Presence.UNKNOWN


class TestPulseAnimation:
    def test_pulse_timer_starts_when_at_desk(self):
        applet = _make_applet()
        applet._state.presence = Presence.AT_DESK
        scheduled: list[int] = []
        with patch(
            "docking.applets.deskpresence.applet.GLib.timeout_add",
            side_effect=lambda ms, _fn: (scheduled.append(ms), 42)[1],
        ):
            applet._ensure_pulse_timer()
        assert scheduled == [60]
        assert applet._pulse_timer_id == 42

    def test_pulse_timer_stops_when_not_at_desk(self):
        applet = _make_applet()
        applet._pulse_timer_id = 99
        applet._pulse_phase = 0.5
        applet._state.presence = Presence.AWAY
        removed: list[int] = []
        with patch(
            "docking.applets.deskpresence.applet.GLib.source_remove",
            side_effect=lambda tid: removed.append(tid),
        ):
            applet._ensure_pulse_timer()
        assert removed == [99]
        assert applet._pulse_timer_id == 0
        assert applet._pulse_phase == 0.0

    def test_pulse_tick_advances_phase_and_repaints(self):
        applet = _make_applet()
        applet._state.presence = Presence.AT_DESK
        applet._pulse_phase = 0.4
        repainted: list[bool] = []
        applet._notify = lambda: repainted.append(True)
        applet._pulse_tick()
        assert applet._pulse_phase > 0.4
        assert repainted == [True]

    def test_pulse_phase_wraps(self):
        applet = _make_applet()
        applet._state.presence = Presence.AT_DESK
        applet._pulse_phase = 0.99
        applet._pulse_tick()
        assert 0.0 <= applet._pulse_phase < 1.0


class TestAppletMenu:
    def test_menu_has_reset_and_threshold_submenu(self):
        applet = _make_applet()
        items = applet.get_menu_items()
        labels = [mi.get_label() for mi in items]
        assert any("Idle Threshold" in label for label in labels)
        assert any("Reset Today" in label for label in labels)

    def test_reset_today_zeros_counters(self):
        config = Config(applet_prefs={})
        applet = _make_applet(config=config)
        applet._state.at_desk_seconds = 9999.0
        applet._state.away_seconds = 1234.0
        applet._reset_today()
        assert applet._state.at_desk_seconds == 0.0
        assert applet._state.away_seconds == 0.0

    def test_threshold_preset_updates_state(self):
        applet = _make_applet()
        applet._set_threshold(seconds=600.0)
        assert applet._state.idle_threshold_s == 600.0


class TestAppletPrefs:
    def test_saves_running_totals(self):
        config = Config(applet_prefs={})
        applet = _make_applet(config=config)
        applet._state.at_desk_seconds = 3600.0
        applet._state.away_seconds = 120.0
        applet._save_prefs()
        saved = config.applet_prefs["deskpresence"]
        assert saved["at_desk_seconds"] == 3600.0
        assert saved["away_seconds"] == 120.0

    def test_loads_same_day_from_config(self):
        today_iso = date.today().isoformat()
        config = Config(
            applet_prefs={
                "deskpresence": {
                    "today": today_iso,
                    "at_desk_seconds": 777.0,
                    "away_seconds": 333.0,
                    "idle_threshold_s": 240.0,
                }
            }
        )
        applet = _make_applet(config=config)
        assert applet._state.idle_threshold_s == 240.0
