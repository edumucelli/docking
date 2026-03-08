"""Pure state and formatting logic for Pomodoro applet."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from docking.i18n import _

# Default durations in minutes
DEFAULT_WORK = 25
DEFAULT_BREAK = 5
DEFAULT_LONG_BREAK = 15
LONG_BREAK_EVERY = 4

# Duration presets for menu radio groups
WORK_PRESETS = (15, 25, 30, 45)
BREAK_PRESETS = (5, 10)
LONG_BREAK_PRESETS = (15, 20, 30)


class State(Enum):
    IDLE = "idle"
    WORK = "work"
    BREAK = "break"
    LONG_BREAK = "long_break"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class PomodoroState:
    """State for Pomodoro behavior."""

    phase: State = State.IDLE
    paused_from: State = State.WORK
    remaining: int = 0
    work_count: int = 0
    work_min: int = DEFAULT_WORK
    break_min: int = DEFAULT_BREAK
    long_break_min: int = DEFAULT_LONG_BREAK
    show_timer: bool = True


@dataclass(frozen=True, slots=True)
class TickResult:
    """Outcome of a timer tick."""

    state: PomodoroState
    phase_changed: bool


def format_time(seconds: int) -> str:
    """Format seconds as MM:SS."""
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


def tooltip_text(state: State, remaining: int) -> str:
    """Build tooltip string for given state."""
    if state == State.IDLE:
        return _("Pomodoro")
    if state == State.PAUSED:
        return _("Paused - {time}").format(time=format_time(seconds=remaining))
    labels = {
        State.WORK: _("Work"),
        State.BREAK: _("Break"),
        State.LONG_BREAK: _("Long Break"),
    }
    return _("{label}: {time} remaining").format(
        label=labels[state], time=format_time(seconds=remaining)
    )


def state_from_prefs(prefs: Mapping[str, Any] | None) -> PomodoroState:
    """Build applet state from persisted preferences."""
    if not prefs:
        return PomodoroState()
    return PomodoroState(
        work_min=int(prefs.get("work", DEFAULT_WORK)),
        break_min=int(prefs.get("break_", DEFAULT_BREAK)),
        long_break_min=int(prefs.get("long_break", DEFAULT_LONG_BREAK)),
        show_timer=bool(prefs.get("show_timer", True)),
    )


def prefs_from_state(state: PomodoroState) -> dict[str, object]:
    """Return preferences payload to persist."""
    return {
        "work": state.work_min,
        "break_": state.break_min,
        "long_break": state.long_break_min,
        "show_timer": state.show_timer,
    }


def start_work(state: PomodoroState) -> PomodoroState:
    """Start a work phase."""
    return replace(state, phase=State.WORK, remaining=state.work_min * 60)


def auto_transition(state: PomodoroState) -> PomodoroState:
    """Transition to next phase when timer expires."""
    if state.phase == State.WORK:
        next_work_count = state.work_count + 1
        if next_work_count % LONG_BREAK_EVERY == 0:
            return replace(
                state,
                phase=State.LONG_BREAK,
                remaining=state.long_break_min * 60,
                work_count=next_work_count,
            )
        return replace(
            state,
            phase=State.BREAK,
            remaining=state.break_min * 60,
            work_count=next_work_count,
        )
    if state.phase in (State.BREAK, State.LONG_BREAK):
        return start_work(state=state)
    return state


def click_toggle(state: PomodoroState) -> PomodoroState:
    """Handle applet click: idle->work, running->pause, paused->resume."""
    if state.phase == State.IDLE:
        return start_work(state=state)
    if state.phase == State.PAUSED:
        return replace(state, phase=state.paused_from)
    return replace(state, paused_from=state.phase, phase=State.PAUSED)


def tick(state: PomodoroState) -> TickResult:
    """Advance one second and return transition result."""
    if state.phase in (State.IDLE, State.PAUSED):
        return TickResult(state=state, phase_changed=False)

    next_state = replace(state, remaining=state.remaining - 1)
    if next_state.remaining <= 0:
        return TickResult(state=auto_transition(state=next_state), phase_changed=True)
    return TickResult(state=next_state, phase_changed=False)


def reset(state: PomodoroState) -> PomodoroState:
    """Reset applet back to idle."""
    return replace(state, phase=State.IDLE, remaining=0, work_count=0)


def set_show_timer(state: PomodoroState, show_timer: bool) -> PomodoroState:
    """Toggle timer text overlay rendering."""
    return replace(state, show_timer=show_timer)


def set_work_minutes(state: PomodoroState, minutes: int) -> PomodoroState:
    """Set work duration in minutes."""
    return replace(state, work_min=minutes)


def set_break_minutes(state: PomodoroState, minutes: int) -> PomodoroState:
    """Set short break duration in minutes."""
    return replace(state, break_min=minutes)


def set_long_break_minutes(state: PomodoroState, minutes: int) -> PomodoroState:
    """Set long break duration in minutes."""
    return replace(state, long_break_min=minutes)
