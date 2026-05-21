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

"""Pure state and accounting logic for desk-presence applet."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, cast

from docking.core.math import clamp
from docking.i18n import _

DEFAULT_IDLE_THRESHOLD_S = 120.0
DEFAULT_POLL_INTERVAL_S = 10.0
MIN_IDLE_THRESHOLD_S = 30.0
MAX_IDLE_THRESHOLD_S = 3600.0

# How many past days to keep alongside "today" for the weekly view.
MAX_HISTORY_DAYS = 6


class Presence(str, Enum):
    AT_DESK = "at_desk"
    AWAY = "away"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class DayEntry:
    """Counters for one past calendar day (UTC)."""

    date: str  # ISO date
    at_desk_seconds: float
    away_seconds: float


@dataclass
class PresenceState:
    """Mutable runtime state for the applet."""

    today: date
    at_desk_seconds: float = 0.0
    away_seconds: float = 0.0
    presence: Presence = Presence.UNKNOWN
    session_start_epoch: float = 0.0
    idle_threshold_s: float = DEFAULT_IDLE_THRESHOLD_S
    # Newest-first rolling history of finalized past days (excludes today).
    history: list[DayEntry] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DeskpresencePrefs:
    """Persisted preferences, today's running totals, and weekly history."""

    today: str = ""
    at_desk_seconds: float = 0.0
    away_seconds: float = 0.0
    idle_threshold_s: float = DEFAULT_IDLE_THRESHOLD_S
    history: tuple[DayEntry, ...] = ()


def _today_utc(now: datetime | None = None) -> date:
    current = now or datetime.now(timezone.utc)
    return current.date()


def presence_from_idle(*, idle_ms: int | None, threshold_s: float) -> Presence:
    """Classify idle time into a Presence value."""
    if idle_ms is None:
        return Presence.UNKNOWN
    if idle_ms < 0:
        return Presence.UNKNOWN
    if idle_ms / 1000.0 >= threshold_s:
        return Presence.AWAY
    return Presence.AT_DESK


def _archive_today(state: PresenceState) -> None:
    """Push the currently-tracked day into history if it had any activity."""
    if state.at_desk_seconds <= 0 and state.away_seconds <= 0:
        return
    entry = DayEntry(
        date=state.today.isoformat(),
        at_desk_seconds=state.at_desk_seconds,
        away_seconds=state.away_seconds,
    )
    # Newest-first; drop any existing entry for the same date so a mid-day
    # date change cannot duplicate it.
    filtered = [d for d in state.history if d.date != entry.date]
    state.history = [entry, *filtered][:MAX_HISTORY_DAYS]


def apply_tick(
    *,
    state: PresenceState,
    idle_ms: int | None,
    now_epoch: float,
    today: date | None = None,
) -> PresenceState:
    """Advance ``state`` by one poll tick, crediting the elapsed seconds.

    - Idle >= threshold -> AWAY bucket.
    - Idle < threshold  -> AT_DESK bucket.
    - Unknown -> no credit, just update timestamp.
    - Date rollover archives today's counters to history and resets.
    """
    current_day = today or _today_utc()
    if state.today != current_day:
        _archive_today(state=state)
        state.today = current_day
        state.at_desk_seconds = 0.0
        state.away_seconds = 0.0
        state.session_start_epoch = now_epoch

    new_presence = presence_from_idle(
        idle_ms=idle_ms, threshold_s=state.idle_threshold_s
    )
    if state.session_start_epoch == 0.0:
        state.session_start_epoch = now_epoch

    elapsed = max(0.0, now_epoch - state.session_start_epoch)
    # Credit elapsed time to whichever bucket was active before this tick,
    # but only when we actually knew the previous state.
    if state.presence is Presence.AT_DESK:
        state.at_desk_seconds += elapsed
    elif state.presence is Presence.AWAY:
        state.away_seconds += elapsed

    if new_presence != state.presence:
        state.presence = new_presence
    state.session_start_epoch = now_epoch
    return state


def week_at_desk_seconds(state: PresenceState) -> float:
    """Sum of at-desk seconds across today + history."""
    return state.at_desk_seconds + sum(d.at_desk_seconds for d in state.history)


def week_away_seconds(state: PresenceState) -> float:
    """Sum of away seconds across today + history."""
    return state.away_seconds + sum(d.away_seconds for d in state.history)


def format_duration(seconds: float) -> str:
    """Compact "Nh Mm" or "Mm" label."""
    total = int(max(0.0, seconds))
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours > 0:
        return _("{h}h {m}m").format(h=hours, m=minutes)
    return _("{m}m").format(m=minutes)


def format_badge(seconds: float) -> str:
    """Short label that fits below the icon: '4h', '34m', or ''."""
    total = int(max(0.0, seconds))
    if total < 60:
        return ""
    hours = total // 3600
    minutes = (total % 3600) // 60
    if hours >= 1:
        return _("{h}h").format(h=hours)
    return _("{m}m").format(m=minutes)


def presence_label(presence: Presence) -> str:
    return {
        Presence.AT_DESK: _("At desk"),
        Presence.AWAY: _("Away"),
        Presence.UNKNOWN: _("Unknown"),
    }[presence]


# Hand-rolled weekday abbreviations so the tooltip is stable regardless of
# the user's LC_TIME setting (strftime("%a") would localize these).
_WEEKDAY_ABBREV = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _weekday_abbrev(d: date) -> str:
    return _WEEKDAY_ABBREV[d.weekday()]


def _weekday_label(iso: str) -> str:
    try:
        return _weekday_abbrev(date.fromisoformat(iso))
    except ValueError:
        return iso


def build_tooltip(*, state: PresenceState, now_epoch: float) -> str:
    lines = [_("Desk Presence")]
    lines.append(_("Status: {s}").format(s=presence_label(state.presence)))
    session_seconds = max(0.0, now_epoch - state.session_start_epoch)
    lines.append(_("Session: {d}").format(d=format_duration(session_seconds)))
    lines.append(
        _("Today at desk: {d}").format(d=format_duration(state.at_desk_seconds))
    )
    lines.append(_("Today away: {d}").format(d=format_duration(state.away_seconds)))

    if state.history:
        lines.append("")
        lines.append(_("Last 7 days"))
        # Today row first.
        lines.append(
            _("  {day}: {d}").format(
                day=_weekday_abbrev(state.today),
                d=format_duration(state.at_desk_seconds),
            )
        )
        for entry in state.history:
            lines.append(
                _("  {day}: {d}").format(
                    day=_weekday_label(entry.date),
                    d=format_duration(entry.at_desk_seconds),
                )
            )
        week_seconds = week_at_desk_seconds(state)
        lines.append(
            _("Week total at desk: {d}").format(d=format_duration(week_seconds))
        )
    return "\n".join(lines)


def _parse_history(raw: object, *, today: date) -> list[DayEntry]:
    if not isinstance(raw, list):
        return []
    parsed: list[DayEntry] = []
    cutoff = today - timedelta(days=MAX_HISTORY_DAYS)
    for rd in raw:
        if not isinstance(rd, dict):
            continue
        entry = cast(dict[str, Any], rd)
        iso = str(entry.get("date", ""))
        if not iso:
            continue
        try:
            entry_day = date.fromisoformat(iso)
        except ValueError:
            continue
        if entry_day >= today or entry_day < cutoff:
            # Skip current-day leftovers (belongs in "today" bucket) and
            # anything older than the rolling window.
            continue
        try:
            at_desk = float(entry.get("at_desk_seconds", 0.0))
            away = float(entry.get("away_seconds", 0.0))
        except (TypeError, ValueError):
            continue
        parsed.append(
            DayEntry(
                date=iso,
                at_desk_seconds=max(0.0, at_desk),
                away_seconds=max(0.0, away),
            )
        )
    parsed.sort(key=lambda d: d.date, reverse=True)
    return parsed[:MAX_HISTORY_DAYS]


def prefs_from_mapping(prefs: Mapping[str, Any] | None) -> DeskpresencePrefs:
    if not prefs:
        return DeskpresencePrefs()
    try:
        today = str(prefs.get("today", ""))
        at_desk = float(prefs.get("at_desk_seconds", 0.0))
        away = float(prefs.get("away_seconds", 0.0))
        threshold = float(prefs.get("idle_threshold_s", DEFAULT_IDLE_THRESHOLD_S))
    except (TypeError, ValueError):
        return DeskpresencePrefs()
    threshold = clamp(threshold, MIN_IDLE_THRESHOLD_S, MAX_IDLE_THRESHOLD_S)
    raw_history = prefs.get("history")
    history = _parse_history(raw_history, today=_today_utc())
    return DeskpresencePrefs(
        today=today,
        at_desk_seconds=max(0.0, at_desk),
        away_seconds=max(0.0, away),
        idle_threshold_s=threshold,
        history=tuple(history),
    )


def prefs_payload(*, state: PresenceState) -> dict[str, Any]:
    return {
        "today": state.today.isoformat(),
        "at_desk_seconds": state.at_desk_seconds,
        "away_seconds": state.away_seconds,
        "idle_threshold_s": state.idle_threshold_s,
        "history": [
            {
                "date": d.date,
                "at_desk_seconds": d.at_desk_seconds,
                "away_seconds": d.away_seconds,
            }
            for d in state.history
        ],
    }


def state_from_prefs(
    *,
    prefs: DeskpresencePrefs,
    today: date | None = None,
) -> PresenceState:
    """Rehydrate state from persisted preferences.

    When the saved "today" is older than the actual today, those counters
    are folded into ``history`` rather than dropped, so a restart after
    midnight does not lose yesterday's numbers.
    """
    current_day = today or _today_utc()
    saved_day: date | None = None
    if prefs.today:
        try:
            saved_day = date.fromisoformat(prefs.today)
        except ValueError:
            saved_day = None

    history: list[DayEntry] = list(prefs.history)
    at_desk = 0.0
    away = 0.0
    if saved_day == current_day:
        at_desk = prefs.at_desk_seconds
        away = prefs.away_seconds
    elif (
        saved_day is not None
        and saved_day < current_day
        and (prefs.at_desk_seconds > 0 or prefs.away_seconds > 0)
    ):
        cutoff = current_day - timedelta(days=MAX_HISTORY_DAYS)
        if saved_day >= cutoff:
            carried = DayEntry(
                date=saved_day.isoformat(),
                at_desk_seconds=prefs.at_desk_seconds,
                away_seconds=prefs.away_seconds,
            )
            history = [
                carried,
                *[d for d in history if d.date != carried.date],
            ][:MAX_HISTORY_DAYS]

    return PresenceState(
        today=current_day,
        at_desk_seconds=at_desk,
        away_seconds=away,
        idle_threshold_s=prefs.idle_threshold_s,
        history=history,
    )
