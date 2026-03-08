"""Pure state and formatting logic for hydration applet."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from docking.i18n import _

DEFAULT_INTERVAL = 45
INTERVAL_PRESETS = (15, 30, 45, 60, 90)
# Refresh icon every N ticks (seconds) when timer overlay is hidden
REDRAW_EVERY = 10


@dataclass(frozen=True, slots=True)
class HydrationState:
    """State for hydration applet behavior."""

    fill: float = 1.0
    interval_min: int = DEFAULT_INTERVAL
    show_timer: bool = False
    tick_count: int = 0


@dataclass(frozen=True, slots=True)
class TickResult:
    """Outcome of one applet tick."""

    state: HydrationState
    became_empty: bool
    should_refresh: bool


def _clamp_fill(fill: float) -> float:
    return max(0.0, min(1.0, fill))


def water_color() -> tuple[float, float, float]:
    """Return the water color (constant vivid blue)."""
    return (0.2, 0.5, 1.0)


def format_remaining(fill: float, interval_min: int) -> str:
    """Format remaining time as M:SS."""
    remaining = int(_clamp_fill(fill) * interval_min * 60)
    minutes = remaining // 60
    seconds = remaining % 60
    return f"{minutes}:{seconds:02d}"


def tooltip_text(fill: float, interval_min: int) -> str:
    """Build tooltip string."""
    if fill <= 0:
        return _("Drink water!")
    return _("Next in {time}").format(
        time=format_remaining(fill=fill, interval_min=interval_min)
    )


def mouth_curvature(fill: float) -> float:
    """Map fill level to mouth mood in [-1, 1].

    1.0 = full smile, 0.0 = neutral, -1.0 = full frown.
    """
    clamped = _clamp_fill(fill)
    return clamped * 2.0 - 1.0


def state_from_prefs(prefs: Mapping[str, Any] | None) -> HydrationState:
    """Build applet state from persisted preferences."""
    if not prefs:
        return HydrationState()
    interval = int(prefs.get("interval", DEFAULT_INTERVAL))
    show_timer = bool(prefs.get("show_timer", False))
    return HydrationState(interval_min=interval, show_timer=show_timer)


def prefs_from_state(state: HydrationState) -> dict[str, object]:
    """Return preferences payload to persist."""
    return {"interval": state.interval_min, "show_timer": state.show_timer}


def refill(state: HydrationState) -> HydrationState:
    """Handle user click refill (drank water)."""
    return replace(state, fill=1.0, tick_count=0)


def set_interval(state: HydrationState, minutes: int) -> HydrationState:
    """Set reminder interval in minutes."""
    return replace(state, interval_min=minutes)


def set_show_timer(state: HydrationState, show_timer: bool) -> HydrationState:
    """Toggle timer overlay rendering."""
    return replace(state, show_timer=show_timer)


def with_fill(state: HydrationState, fill: float) -> HydrationState:
    """Return state with fill clamped to [0, 1]."""
    return replace(state, fill=_clamp_fill(fill))


def tick(state: HydrationState) -> TickResult:
    """Advance one second and compute render/update intent."""
    if state.fill <= 0:
        return TickResult(state=state, became_empty=False, should_refresh=False)

    total_secs = max(1, state.interval_min * 60)
    next_fill = _clamp_fill(state.fill - 1.0 / total_secs)
    next_tick = state.tick_count + 1
    next_state = replace(state, fill=next_fill, tick_count=next_tick)

    became_empty = state.fill > 0 and next_fill <= 0
    should_refresh = (
        became_empty or next_state.show_timer or next_tick % REDRAW_EVERY == 0
    )
    return TickResult(
        state=next_state,
        became_empty=became_empty,
        should_refresh=should_refresh,
    )
