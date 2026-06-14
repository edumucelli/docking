# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Pure scheduling and formatting logic for the Alarm applet."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from docking.core.math import clamp_int
from docking.i18n import _

DEFAULT_SNOOZE_MINUTES = 5
DEFAULT_LABEL = "Alarm"
TICK_SECONDS = 30
RINGING_TICK_SECONDS = 1
WEEKDAY_LABELS = (
    _("Mon"),
    _("Tue"),
    _("Wed"),
    _("Thu"),
    _("Fri"),
    _("Sat"),
    _("Sun"),
)


@dataclass(frozen=True, slots=True)
class AlarmPreset:
    """One configured alarm preset."""

    label: str = DEFAULT_LABEL
    hour: int = 7
    minute: int = 0
    enabled: bool = True
    repeat_days: tuple[int, ...] = ()
    snooze_minutes: int = DEFAULT_SNOOZE_MINUTES
    last_triggered: str = ""
    snoozed_until: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class ScheduledAlarm:
    """A concrete future occurrence of an alarm preset."""

    index: int
    preset: AlarmPreset
    when: dt.datetime


@dataclass(frozen=True, slots=True)
class AlarmState:
    """Runtime state for all alarm presets."""

    presets: tuple[AlarmPreset, ...] = ()
    ringing_index: int | None = None
    ringing_since: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class TickResult:
    """Outcome of a scheduler tick."""

    state: AlarmState
    started_ringing: bool
    should_refresh: bool


def state_from_prefs(prefs: Mapping[str, Any] | None) -> AlarmState:
    """Build alarm state from persisted applet preferences."""
    if not prefs:
        return AlarmState()
    raw_presets = prefs.get("presets")
    if not isinstance(raw_presets, list | tuple):
        return AlarmState()
    presets = tuple(
        preset
        for raw in raw_presets
        if (preset := _preset_from_mapping(raw)) is not None
    )
    return AlarmState(presets=presets)


def prefs_from_state(state: AlarmState) -> dict[str, object]:
    """Return the persistent preferences payload."""
    return {
        "presets": [
            {
                "label": preset.label,
                "hour": preset.hour,
                "minute": preset.minute,
                "enabled": preset.enabled,
                "repeat_days": list(preset.repeat_days),
                "snooze_minutes": preset.snooze_minutes,
                "last_triggered": preset.last_triggered,
                "snoozed_until": _datetime_payload(preset.snoozed_until),
            }
            for preset in state.presets
        ]
    }


def add_preset(state: AlarmState, preset: AlarmPreset) -> AlarmState:
    """Append a new alarm preset."""
    return replace(state, presets=(*state.presets, normalize_preset(preset)))


def remove_preset(state: AlarmState, index: int) -> AlarmState:
    """Remove an alarm preset by index."""
    if not _valid_index(state=state, index=index):
        return state
    presets = tuple(preset for i, preset in enumerate(state.presets) if i != index)
    ringing_index = state.ringing_index
    if ringing_index == index:
        ringing_index = None
    elif ringing_index is not None and ringing_index > index:
        ringing_index -= 1
    return AlarmState(
        presets=presets,
        ringing_index=ringing_index,
        ringing_since=state.ringing_since if ringing_index is not None else None,
    )


def replace_preset(state: AlarmState, index: int, preset: AlarmPreset) -> AlarmState:
    """Replace a configured preset."""
    if not _valid_index(state=state, index=index):
        return state
    presets = tuple(
        normalize_preset(preset) if i == index else existing
        for i, existing in enumerate(state.presets)
    )
    return replace(state, presets=presets)


def set_enabled(state: AlarmState, index: int, enabled: bool) -> AlarmState:
    """Enable or disable one alarm preset."""
    if not _valid_index(state=state, index=index):
        return state
    preset = replace(
        state.presets[index],
        enabled=enabled,
        snoozed_until=None if not enabled else state.presets[index].snoozed_until,
    )
    return replace_preset(state=state, index=index, preset=preset)


def dismiss_ringing(state: AlarmState) -> AlarmState:
    """Dismiss the currently ringing alarm."""
    return replace(state, ringing_index=None, ringing_since=None)


def snooze_ringing(state: AlarmState, *, now: dt.datetime) -> AlarmState:
    """Snooze the currently ringing alarm using its configured duration."""
    if state.ringing_index is None:
        return state
    preset = state.presets[state.ringing_index]
    snoozed_until = now + dt.timedelta(minutes=preset.snooze_minutes)
    state = replace_preset(
        state=state,
        index=state.ringing_index,
        preset=replace(preset, enabled=True, snoozed_until=snoozed_until),
    )
    return replace(state, ringing_index=None, ringing_since=None)


def next_alarm(state: AlarmState, *, now: dt.datetime) -> ScheduledAlarm | None:
    """Return the next enabled alarm occurrence after ``now``."""
    scheduled = [
        ScheduledAlarm(index=index, preset=preset, when=when)
        for index, preset in enumerate(state.presets)
        if preset.enabled
        if (when := _next_occurrence(preset=preset, now=now)) is not None
    ]
    if not scheduled:
        return None
    return min(scheduled, key=lambda alarm: alarm.when)


def tick(state: AlarmState, *, now: dt.datetime) -> TickResult:
    """Advance alarm scheduling and start ringing when an alarm is due."""
    if state.ringing_index is not None:
        return TickResult(state=state, started_ringing=False, should_refresh=True)

    due = _due_alarm(state=state, now=now)
    if due is None:
        return TickResult(state=state, started_ringing=False, should_refresh=True)

    preset = due.preset
    trigger_key = _trigger_key(preset=preset, when=due.when)
    updated = replace(preset, last_triggered=trigger_key, snoozed_until=None)
    if not preset.repeat_days:
        updated = replace(updated, enabled=False)
    state = replace_preset(state=state, index=due.index, preset=updated)
    return TickResult(
        state=replace(state, ringing_index=due.index, ringing_since=now),
        started_ringing=True,
        should_refresh=True,
    )


def icon_label(state: AlarmState, *, now: dt.datetime) -> str:
    """Return compact dock icon label text."""
    if state.ringing_index is not None:
        return _("Ring")
    alarm = next_alarm(state, now=now)
    if alarm is None:
        return ""
    delta = alarm.when - now
    total_minutes = max(0, int(delta.total_seconds() // 60))
    if total_minutes < 24 * 60:
        hours, minutes = divmod(total_minutes, 60)
        if hours:
            return _("{hours}h").format(hours=hours)
        return _("{minutes}m").format(minutes=minutes)
    return alarm.when.strftime("%a")


def tooltip_text(state: AlarmState, *, now: dt.datetime) -> str:
    """Build tooltip text for the current alarm state."""
    if state.ringing_index is not None:
        preset = state.presets[state.ringing_index]
        return _("Alarm ringing: {label}").format(label=preset.label)
    alarm = next_alarm(state, now=now)
    if alarm is None:
        return _("Alarm: no enabled alarms")
    return _("Next alarm: {label} at {time}").format(
        label=alarm.preset.label,
        time=format_alarm_time(alarm.when),
    )


def menu_status_text(state: AlarmState, *, now: dt.datetime) -> str:
    """Return a short menu status line."""
    if state.ringing_index is not None:
        return tooltip_text(state, now=now)
    alarm = next_alarm(state, now=now)
    if alarm is None:
        return _("No enabled alarms")
    return _("{label}: {time} ({duration})").format(
        label=alarm.preset.label,
        time=format_alarm_time(alarm.when),
        duration=format_duration(alarm.when - now),
    )


def preset_summary(preset: AlarmPreset) -> str:
    """Return a menu-friendly summary for one preset."""
    repeat = repeat_label(preset.repeat_days)
    state = _("on") if preset.enabled else _("off")
    return _("{time} {label} - {repeat}, {state}").format(
        time=format_clock_time(hour=preset.hour, minute=preset.minute),
        label=preset.label,
        repeat=repeat,
        state=state,
    )


def repeat_label(days: Sequence[int]) -> str:
    """Return a human-readable repeat schedule label."""
    repeat_days = tuple(sorted(day for day in days if 0 <= day <= 6))
    if not repeat_days:
        return _("once")
    if repeat_days == (0, 1, 2, 3, 4):
        return _("weekdays")
    if repeat_days == (5, 6):
        return _("weekends")
    if repeat_days == (0, 1, 2, 3, 4, 5, 6):
        return _("daily")
    return ", ".join(WEEKDAY_LABELS[day] for day in repeat_days)


def format_clock_time(*, hour: int, minute: int) -> str:
    """Format a local wall-clock hour and minute."""
    return f"{hour:02d}:{minute:02d}"


def format_alarm_time(value: dt.datetime) -> str:
    """Format a concrete alarm occurrence."""
    if value.date() == dt.datetime.now(value.tzinfo).date():
        return value.strftime("%H:%M")
    return value.strftime("%a %H:%M")


def format_duration(delta: dt.timedelta) -> str:
    """Format time until an alarm."""
    total_minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return _("{days}d {hours}h").format(days=days, hours=hours)
    if hours:
        return _("{hours}h {minutes}m").format(hours=hours, minutes=minutes)
    return _("{minutes}m").format(minutes=minutes)


def normalize_preset(preset: AlarmPreset) -> AlarmPreset:
    """Clamp user-entered preset fields to supported values."""
    label = preset.label.strip() or DEFAULT_LABEL
    repeat_days = tuple(sorted({day for day in preset.repeat_days if 0 <= day <= 6}))
    return replace(
        preset,
        label=label,
        hour=clamp_int(int(preset.hour), 0, 23),
        minute=clamp_int(int(preset.minute), 0, 59),
        repeat_days=repeat_days,
        snooze_minutes=clamp_int(int(preset.snooze_minutes), 1, 120),
    )


def _preset_from_mapping(raw: object) -> AlarmPreset | None:
    if not isinstance(raw, Mapping):
        return None
    raw_dict = {str(key): value for key, value in raw.items()}
    try:
        preset = AlarmPreset(
            label=str(raw_dict.get("label", DEFAULT_LABEL)),
            hour=_parse_int(raw_dict.get("hour"), default=7),
            minute=_parse_int(raw_dict.get("minute"), default=0),
            enabled=bool(raw_dict.get("enabled", True)),
            repeat_days=_parse_repeat_days(raw_dict.get("repeat_days")),
            snooze_minutes=_parse_int(
                raw_dict.get("snooze_minutes"),
                default=DEFAULT_SNOOZE_MINUTES,
            ),
            last_triggered=str(raw_dict.get("last_triggered", "")),
            snoozed_until=_parse_datetime(raw_dict.get("snoozed_until")),
        )
    except (TypeError, ValueError):
        return None
    return normalize_preset(preset)


def _parse_repeat_days(value: object) -> tuple[int, ...]:
    if not isinstance(value, list | tuple):
        return ()
    days: set[int] = set()
    for raw in value:
        day = _parse_int(raw, default=-1)
        if 0 <= day <= 6:
            days.add(day)
    return tuple(sorted(days))


def _parse_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float | str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _parse_datetime(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.astimezone()


def _datetime_payload(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _valid_index(*, state: AlarmState, index: int) -> bool:
    return 0 <= index < len(state.presets)


def _due_alarm(state: AlarmState, *, now: dt.datetime) -> ScheduledAlarm | None:
    due: list[ScheduledAlarm] = []
    for index, preset in enumerate(state.presets):
        if not preset.enabled:
            continue
        when = _due_occurrence(preset=preset, now=now)
        if when is not None:
            due.append(ScheduledAlarm(index=index, preset=preset, when=when))
    if not due:
        return None
    return min(due, key=lambda alarm: alarm.when)


def _due_occurrence(*, preset: AlarmPreset, now: dt.datetime) -> dt.datetime | None:
    if preset.snoozed_until is not None:
        return preset.snoozed_until if preset.snoozed_until <= now else None

    candidate = now.replace(
        hour=preset.hour,
        minute=preset.minute,
        second=0,
        microsecond=0,
    )
    if candidate > now:
        return None
    if preset.repeat_days and candidate.weekday() not in preset.repeat_days:
        return None
    key = _trigger_key(preset=preset, when=candidate)
    if preset.last_triggered == key:
        return None
    return candidate


def _next_occurrence(*, preset: AlarmPreset, now: dt.datetime) -> dt.datetime | None:
    if preset.snoozed_until is not None and preset.snoozed_until > now:
        return preset.snoozed_until

    for offset in range(8):
        day = now.date() + dt.timedelta(days=offset)
        candidate = dt.datetime.combine(
            day,
            dt.time(hour=preset.hour, minute=preset.minute),
            tzinfo=now.tzinfo,
        )
        if candidate <= now:
            continue
        if preset.repeat_days and candidate.weekday() not in preset.repeat_days:
            continue
        if preset.last_triggered == _trigger_key(preset=preset, when=candidate):
            continue
        return candidate
    return None


def _trigger_key(*, preset: AlarmPreset, when: dt.datetime) -> str:
    if preset.snoozed_until is not None:
        return f"snooze:{when.isoformat(timespec='minutes')}"
    return when.date().isoformat()
