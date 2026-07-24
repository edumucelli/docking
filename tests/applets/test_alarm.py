"""Tests for the Alarm applet."""

from __future__ import annotations

import datetime as dt

from docking.applets.alarm.applet import AlarmApplet
from docking.applets.alarm.state import (
    DEFAULT_LABEL,
    WEEKDAY_LABELS,
    AlarmPreset,
    AlarmState,
    _due_occurrence,
    _next_occurrence,
    _parse_datetime,
    _parse_int,
    _parse_repeat_days,
    _preset_from_mapping,
    add_preset,
    dismiss_ringing,
    format_alarm_time,
    format_clock_time,
    format_duration,
    icon_label,
    menu_status_text,
    next_alarm,
    normalize_preset,
    prefs_from_state,
    preset_summary,
    remove_preset,
    repeat_label,
    replace_preset,
    set_enabled,
    snooze_ringing,
    state_from_prefs,
    tick,
    tooltip_text,
)
from docking.core.config import Config

_TZ = dt.timezone.utc


class TestAlarmState:
    def test_prefs_round_trip(self):
        state = AlarmState(
            presets=(
                AlarmPreset(
                    label="Wake",
                    hour=6,
                    minute=30,
                    enabled=True,
                    repeat_days=(0, 1, 2, 3, 4),
                    snooze_minutes=10,
                    last_triggered="2026-05-17",
                ),
            )
        )

        payload = prefs_from_state(state)
        loaded = state_from_prefs(payload)

        assert loaded == state

    def test_next_alarm_uses_nearest_enabled_preset(self):
        now = dt.datetime(2026, 5, 18, 8, 0, tzinfo=_TZ)
        state = AlarmState(
            presets=(
                AlarmPreset(label="Evening", hour=20, minute=0),
                AlarmPreset(label="Morning", hour=9, minute=15),
            )
        )

        alarm = next_alarm(state, now=now)

        assert alarm is not None
        assert alarm.preset.label == "Morning"
        assert alarm.when == dt.datetime(2026, 5, 18, 9, 15, tzinfo=_TZ)

    def test_repeating_alarm_skips_unselected_days(self):
        monday = dt.datetime(2026, 5, 18, 8, 0, tzinfo=_TZ)
        state = AlarmState(presets=(AlarmPreset(hour=7, minute=30, repeat_days=(2,)),))

        alarm = next_alarm(state, now=monday)

        assert alarm is not None
        assert alarm.when == dt.datetime(2026, 5, 20, 7, 30, tzinfo=_TZ)

    def test_one_shot_alarm_disables_after_trigger(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        state = AlarmState(presets=(AlarmPreset(label="Wake", hour=7, minute=0),))

        result = tick(state, now=now)

        assert result.started_ringing
        assert result.state.ringing_index == 0
        assert not result.state.presets[0].enabled
        assert result.state.presets[0].last_triggered == "2026-05-18"

    def test_repeating_alarm_remains_enabled_after_trigger(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        state = AlarmState(
            presets=(AlarmPreset(hour=7, minute=0, repeat_days=(0, 1, 2, 3, 4)),)
        )

        result = tick(state, now=now)

        assert result.started_ringing
        assert result.state.presets[0].enabled
        assert result.state.presets[0].last_triggered == "2026-05-18"

    def test_snooze_moves_alarm_to_future(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        state = AlarmState(
            presets=(AlarmPreset(snooze_minutes=9),),
            ringing_index=0,
            ringing_since=now,
        )

        snoozed = snooze_ringing(state, now=now)

        assert snoozed.ringing_index is None
        assert snoozed.presets[0].snoozed_until == dt.datetime(
            2026, 5, 18, 7, 9, tzinfo=_TZ
        )

    def test_dismiss_clears_ringing_state(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        state = AlarmState(
            presets=(AlarmPreset(),),
            ringing_index=0,
            ringing_since=now,
        )

        dismissed = dismiss_ringing(state)

        assert dismissed.ringing_index is None
        assert dismissed.ringing_since is None

    def test_enable_toggle_clears_snooze_when_disabled(self):
        snoozed_until = dt.datetime(2026, 5, 18, 7, 9, tzinfo=_TZ)
        state = AlarmState(
            presets=(AlarmPreset(snoozed_until=snoozed_until),),
        )

        disabled = set_enabled(state, index=0, enabled=False)

        assert not disabled.presets[0].enabled
        assert disabled.presets[0].snoozed_until is None

    def test_labels(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        state = add_preset(
            AlarmState(),
            AlarmPreset(label="Wake", hour=7, minute=45),
        )

        assert icon_label(state, now=now) == "45m"
        assert "Wake" in tooltip_text(state, now=now)
        assert repeat_label((0, 1, 2, 3, 4)) == "weekdays"


class TestAlarmApplet:
    def test_creates_with_icon(self):
        applet = AlarmApplet(48, config=Config())

        assert applet.item.icon is not None

    def test_icon_renders_at_various_sizes(self):
        applet = AlarmApplet(48, config=Config())
        applet._state = AlarmState(presets=(AlarmPreset(hour=7, minute=30),))

        for size in [32, 48, 64]:
            pixbuf = applet.create_icon(size=size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_add_alarm_saves_preferences(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        applet = AlarmApplet(48, config=config)

        applet._upsert_preset(index=None, preset=AlarmPreset(label="Wake"))

        prefs = Config.load(path).applet_prefs["alarm"]
        assert prefs["presets"][0]["label"] == "Wake"


class TestAlarmStateEdgeCases:
    def test_state_from_prefs_empty_dict(self):
        assert state_from_prefs({}) == AlarmState()

    def test_state_from_prefs_none(self):
        assert state_from_prefs(None) == AlarmState()

    def test_state_from_prefs_non_list_presets(self):
        assert state_from_prefs({"presets": "bad"}) == AlarmState()

    def test_state_from_prefs_invalid_preset_skipped(self):
        assert state_from_prefs({"presets": ["not-a-mapping"]}) == AlarmState()

    def test_remove_preset_invalid_index(self):
        state = AlarmState(presets=(AlarmPreset(label="A"),))
        assert remove_preset(state, index=5) == state

    def test_remove_preset_clears_ringing_index(self):
        state = AlarmState(
            presets=(AlarmPreset(label="A"),),
            ringing_index=0,
            ringing_since=dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ),
        )
        result = remove_preset(state, index=0)
        assert result.ringing_index is None
        assert result.ringing_since is None

    def test_remove_preset_adjusts_ringing_index_when_above(self):
        state = AlarmState(
            presets=(
                AlarmPreset(label="A"),
                AlarmPreset(label="B"),
                AlarmPreset(label="C"),
            ),
            ringing_index=2,
        )
        result = remove_preset(state, index=1)
        assert result.ringing_index == 1

    def test_replace_preset_invalid_index(self):
        state = AlarmState(presets=(AlarmPreset(label="A"),))
        assert replace_preset(state, index=5, preset=AlarmPreset(label="X")) == state

    def test_set_enabled_invalid_index(self):
        state = AlarmState(presets=(AlarmPreset(label="A"),))
        assert set_enabled(state, index=5, enabled=False) == state

    def test_snooze_when_not_ringing(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        state = AlarmState(ringing_index=None)
        assert snooze_ringing(state, now=now) == state

    def test_tick_when_already_ringing(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        state = AlarmState(
            presets=(AlarmPreset(label="Wake"),),
            ringing_index=0,
            ringing_since=now,
        )
        result = tick(state, now=now)
        assert not result.started_ringing

    def test_tick_no_due_alarms(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        state = AlarmState(presets=())
        result = tick(state, now=now)
        assert not result.started_ringing

    def test_icon_label_when_ringing(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        state = AlarmState(
            presets=(AlarmPreset(label="Wake"),),
            ringing_index=0,
            ringing_since=now,
        )
        assert icon_label(state, now=now) != ""

    def test_icon_label_no_next_alarm(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        assert icon_label(AlarmState(), now=now) == ""

    def test_icon_label_more_than_24h(self):
        """Alarm more than 24h away shows weekday abbreviation."""
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        state = AlarmState(
            presets=(AlarmPreset(label="Far", hour=7, minute=0, repeat_days=(2,)),)
        )
        assert icon_label(state, now=now) == "Wed"

    def test_icon_label_with_hours(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        state = AlarmState(presets=(AlarmPreset(label="Wake", hour=10, minute=0),))
        result = icon_label(state, now=now)
        assert "3" in result

    def test_tooltip_text_when_ringing(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        state = AlarmState(
            presets=(AlarmPreset(label="Wake"),),
            ringing_index=0,
            ringing_since=now,
        )
        assert "Wake" in tooltip_text(state, now=now)

    def test_tooltip_text_no_alarms(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        assert "no enabled alarms" in tooltip_text(AlarmState(), now=now)

    def test_menu_status_text_ringing(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        state = AlarmState(
            presets=(AlarmPreset(label="Wake"),),
            ringing_index=0,
            ringing_since=now,
        )
        assert "Wake" in menu_status_text(state, now=now)

    def test_menu_status_text_no_alarms(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        assert "No enabled alarms" in menu_status_text(AlarmState(), now=now)

    def test_menu_status_text_with_alarm(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        state = add_preset(AlarmState(), AlarmPreset(label="Wake", hour=8, minute=30))
        assert "Wake" in menu_status_text(state, now=now)

    def test_preset_summary_disabled(self):
        preset = AlarmPreset(label="Wake", enabled=False)
        assert "off" in preset_summary(preset)

    def test_preset_summary_enabled(self):
        preset = AlarmPreset(label="Wake", enabled=True, hour=7, minute=30)
        assert "07:30 Wake" in preset_summary(preset)

    def test_repeat_label_empty(self):
        assert repeat_label(()) == "once"

    def test_repeat_label_weekends(self):
        assert repeat_label((5, 6)) == "weekends"

    def test_repeat_label_daily(self):
        assert repeat_label(tuple(range(7))) == "daily"

    def test_repeat_label_custom_days(self):
        result = repeat_label((0, 2, 4))
        assert result == "Mon, Wed, Fri"

    def test_repeat_label_filters_invalid_days(self):
        assert repeat_label((0, 7, -1)) == "Mon"

    def test_format_clock_time(self):
        assert format_clock_time(hour=7, minute=5) == "07:05"

    def test_format_alarm_time_today(self, monkeypatch):
        monkeypatch.setattr("docking.applets.alarm.state.dt.datetime", dt.datetime)
        alarm_time = dt.datetime(2026, 5, 18, 14, 30, tzinfo=_TZ)
        result = format_alarm_time(alarm_time)
        assert "14:30" in result

    def test_format_alarm_time_other_day(self):
        alarm_time = dt.datetime(2026, 5, 20, 14, 30, tzinfo=_TZ)
        result = format_alarm_time(alarm_time)
        assert result != "14:30"

    def test_format_duration_days(self):
        delta = dt.timedelta(days=2, hours=3)
        result = format_duration(delta)
        assert "2d" in result
        assert "3h" in result

    def test_format_duration_hours(self):
        delta = dt.timedelta(hours=5, minutes=30)
        result = format_duration(delta)
        assert "5h" in result
        assert "30m" in result

    def test_format_duration_minutes(self):
        delta = dt.timedelta(minutes=45)
        result = format_duration(delta)
        assert "45m" in result

    def test_format_duration_zero(self):
        delta = dt.timedelta(seconds=0)
        result = format_duration(delta)
        assert result != ""

    def test_normalize_preset_clamps_values(self):
        preset = AlarmPreset(label="  Test  ", hour=25, minute=60, snooze_minutes=200)
        normalized = normalize_preset(preset)
        assert normalized.label == "Test"
        assert normalized.hour == 23
        assert normalized.minute == 59
        assert normalized.snooze_minutes == 120

    def test_normalize_preset_empty_label_uses_default(self):
        preset = AlarmPreset(label="   ")
        normalized = normalize_preset(preset)
        assert normalized.label == DEFAULT_LABEL


class TestAlarmHelpers:
    def test_parse_int_bool(self):
        assert _parse_int(True, default=7) == 1
        assert _parse_int(False, default=7) == 0

    def test_parse_int_float(self):
        assert _parse_int(3.14, default=7) == 3

    def test_parse_int_string(self):
        assert _parse_int("42", default=7) == 42

    def test_parse_int_invalid_string(self):
        assert _parse_int("abc", default=7) == 7

    def test_parse_int_none(self):
        assert _parse_int(None, default=7) == 7

    def test_parse_repeat_days_non_list(self):
        assert _parse_repeat_days("bad") == ()

    def test_parse_repeat_days_valid(self):
        assert _parse_repeat_days([0, 2, 4, 7, -1]) == (0, 2, 4)

    def test_parse_datetime_empty_string(self):
        assert _parse_datetime("") is None

    def test_parse_datetime_non_string(self):
        assert _parse_datetime(42) is None

    def test_parse_datetime_invalid(self):
        assert _parse_datetime("not-a-date") is None

    def test_parse_datetime_naive(self):
        result = _parse_datetime("2026-05-18T07:00:00")
        assert result is not None
        assert result.tzinfo is not None

    def test_preset_from_mapping_non_mapping(self):
        assert _preset_from_mapping("bad") is None

    def test_due_occurrence_snoozed_future(self, monkeypatch):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        snoozed = dt.datetime(2026, 5, 18, 7, 15, tzinfo=_TZ)
        preset = AlarmPreset(hour=7, minute=0, snoozed_until=snoozed)
        assert _due_occurrence(preset=preset, now=now) is None

    def test_due_occurrence_future_alarm(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        preset = AlarmPreset(hour=8, minute=0)
        assert _due_occurrence(preset=preset, now=now) is None

    def test_due_occurrence_wrong_day(self):
        now = dt.datetime(2026, 5, 19, 7, 0, tzinfo=_TZ)  # Tuesday
        preset = AlarmPreset(hour=6, minute=0, repeat_days=(0, 2, 4))  # M,W,F
        assert _due_occurrence(preset=preset, now=now) is None

    def test_due_occurrence_already_triggered(self):
        now = dt.datetime(2026, 5, 18, 7, 1, tzinfo=_TZ)
        preset = AlarmPreset(hour=7, minute=0, last_triggered="2026-05-18")
        assert _due_occurrence(preset=preset, now=now) is None

    def test_due_occurrence_returns_snoozed_when_due(self):
        now = dt.datetime(2026, 5, 18, 7, 20, tzinfo=_TZ)
        snoozed = dt.datetime(2026, 5, 18, 7, 15, tzinfo=_TZ)
        preset = AlarmPreset(hour=7, minute=0, snoozed_until=snoozed)
        result = _due_occurrence(preset=preset, now=now)
        assert result == snoozed

    def test_next_occurrence_snoozed(self):
        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        snoozed = dt.datetime(2026, 5, 18, 7, 15, tzinfo=_TZ)
        preset = AlarmPreset(hour=7, minute=0, snoozed_until=snoozed)
        result = _next_occurrence(preset=preset, now=now)
        assert result == snoozed

    def test_next_occurrence_no_repeat_no_match(self):
        """A one-shot alarm that already triggered today will fire tomorrow."""
        now = dt.datetime(2026, 5, 18, 9, 0, tzinfo=_TZ)
        preset = AlarmPreset(hour=8, minute=0, last_triggered="2026-05-18")
        result = _next_occurrence(preset=preset, now=now)
        assert result is not None
        assert result.date() == dt.date(2026, 5, 19)

    def test_next_occurrence_exhausts_loop_when_all_dates_skip(self):
        """When last_triggered matches the only future repeat day, return None."""
        now = dt.datetime(2026, 5, 18, 9, 0, tzinfo=_TZ)  # Monday
        preset = AlarmPreset(
            hour=8,
            minute=0,
            repeat_days=(0,),  # Monday only
            last_triggered="2026-05-25",  # Next Monday
        )
        result = _next_occurrence(preset=preset, now=now)
        assert result is None

    def test_trigger_key_snoozed(self):
        from docking.applets.alarm.state import _trigger_key

        preset = AlarmPreset(snoozed_until=dt.datetime(2026, 5, 18, 7, 15, tzinfo=_TZ))
        when = dt.datetime(2026, 5, 18, 7, 15, tzinfo=_TZ)
        result = _trigger_key(preset=preset, when=when)
        assert result.startswith("snooze:")

    def test_due_alarm_empty_state(self):
        from docking.applets.alarm.state import _due_alarm

        now = dt.datetime(2026, 5, 18, 7, 0, tzinfo=_TZ)
        assert _due_alarm(AlarmState(), now=now) is None

    def test_due_alarm_disabled_presets_skipped(self):
        from docking.applets.alarm.state import _due_alarm

        now = dt.datetime(2026, 5, 18, 7, 1, tzinfo=_TZ)
        state = AlarmState(presets=(AlarmPreset(hour=7, minute=0, enabled=False),))
        assert _due_alarm(state, now=now) is None

    def test_preset_from_mapping_type_error(self):
        """A mapping that doesn't have the right key types should return None."""
        raw = {"hour": "not_a_number"}
        result = _preset_from_mapping(raw)
        assert result is not None  # Should handle gracefully

    def test_weekday_labels_length(self):
        assert len(WEEKDAY_LABELS) == 7
