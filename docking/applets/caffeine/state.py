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

"""Pure state and formatting logic for the Caffeine applet.

Caffeine keeps the session awake by inhibiting the screensaver and system
sleep. This module owns only the toggle/countdown logic; the actual inhibition
side effects live in ``inhibit.py``.

The active flag is intentionally never persisted: a lock that silently survived
a restart (or a crash) would be confusing and hard to discover, so the applet
always starts in the off state. Only the auto-off duration preference persists.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from docking.i18n import _

# Auto-off duration presets in minutes. ``INDEFINITE`` keeps the session awake
# until the user turns Caffeine off manually.
INDEFINITE = 0
DURATION_PRESETS: tuple[int, ...] = (INDEFINITE, 15, 30, 60, 120)
DEFAULT_DURATION = INDEFINITE


@dataclass(frozen=True, slots=True)
class CaffeineState:
    """State for Caffeine behavior."""

    active: bool = False
    duration_min: int = DEFAULT_DURATION
    remaining: int = 0


def state_from_prefs(prefs: Mapping[str, Any] | None) -> CaffeineState:
    """Build applet state from persisted preferences (active never persists)."""
    if not prefs:
        return CaffeineState()
    return CaffeineState(duration_min=int(prefs.get("duration_min", DEFAULT_DURATION)))


def prefs_from_state(state: CaffeineState) -> dict[str, object]:
    """Return preferences payload to persist."""
    return {"duration_min": state.duration_min}


def activate(state: CaffeineState) -> CaffeineState:
    """Turn Caffeine on, arming the countdown when a duration is set."""
    remaining = state.duration_min * 60 if state.duration_min else 0
    return replace(state, active=True, remaining=remaining)


def deactivate(state: CaffeineState) -> CaffeineState:
    """Turn Caffeine off and clear any countdown."""
    return replace(state, active=False, remaining=0)


def toggle(state: CaffeineState) -> CaffeineState:
    """Flip the active state."""
    return deactivate(state=state) if state.active else activate(state=state)


def set_duration(state: CaffeineState, minutes: int) -> CaffeineState:
    """Set the auto-off duration, re-arming the countdown when already active."""
    next_state = replace(state, duration_min=minutes)
    if next_state.active:
        return activate(state=next_state)
    return next_state


def tick(state: CaffeineState) -> CaffeineState:
    """Advance one second; deactivate when a timed session elapses."""
    if not has_timer(state=state):
        return state
    remaining = state.remaining - 1
    if remaining <= 0:
        return deactivate(state=state)
    return replace(state, remaining=remaining)


def has_timer(state: CaffeineState) -> bool:
    """Whether a finite countdown is currently running."""
    return state.active and state.duration_min != INDEFINITE


def format_remaining(seconds: int) -> str:
    """Format remaining seconds as MM:SS."""
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


def duration_label(minutes: int) -> str:
    """Human label for an auto-off duration choice."""
    if minutes == INDEFINITE:
        return _("Until turned off")
    return _("{mins} min").format(mins=minutes)


def tooltip_text(state: CaffeineState) -> str:
    """Build tooltip string for the current state."""
    if not state.active:
        return _("Caffeine: off")
    if state.duration_min == INDEFINITE:
        return _("Caffeine: keeping awake")
    return _("Caffeine: {time} remaining").format(
        time=format_remaining(seconds=state.remaining)
    )


def status_text(state: CaffeineState) -> str:
    """Short current-state summary for the menu header row."""
    if not state.active:
        return _("Off")
    if state.duration_min == INDEFINITE:
        return _("Active")
    return _("Active - {time} left").format(
        time=format_remaining(seconds=state.remaining)
    )
