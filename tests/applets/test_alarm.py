"""Tests for the Alarm applet."""

from __future__ import annotations

import datetime as dt

from docking.applets.alarm.applet import AlarmApplet
from docking.applets.alarm.state import (
    AlarmPreset,
    AlarmState,
    add_preset,
    dismiss_ringing,
    icon_label,
    next_alarm,
    prefs_from_state,
    repeat_label,
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
        applet = AlarmApplet(48)

        assert applet.item.icon is not None

    def test_icon_renders_at_various_sizes(self):
        applet = AlarmApplet(48)
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
