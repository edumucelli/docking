"""Pure state logic for the pet applet — no GTK/Cairo."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import NamedTuple

from docking.applets.systemmonitor.state import CpuSample, cpu_percent, parse_proc_stat
from docking.i18n import _

# CPU fraction thresholds (0.0–1.0)
IDLE_CPU = 0.05
RELAXED_CPU = 0.20
MODERATE_CPU = 0.40
BUSY_CPU = 0.60
VERY_BUSY_CPU = 0.80
SPIKE_DELTA = 0.30

# Idle tick counts (each tick = 2s)
DROWSY_TICKS = 30  # 1 min
SLEEPY_TICKS = 60  # 2 min
SLEEPING_TICKS = 150  # 5 min

# Require N consecutive ticks in a new mood before committing
HYSTERESIS = 3

# Skip redraw when CPU delta is tiny and mood unchanged
CPU_REDRAW_THRESHOLD = 0.05

# Smoothing factor for exponential moving average
SMOOTH_ALPHA = 0.3


class Mood(str, Enum):
    SLEEPING = "sleeping"  # 5min+ idle
    SLEEPY = "sleepy"  # 2min+ idle
    DROWSY = "drowsy"  # 1min+ idle
    RELAXED = "relaxed"  # <20% CPU
    HAPPY = "happy"  # 20-40% CPU
    FOCUSED = "focused"  # 40-60% CPU
    BUSY = "busy"  # 60-80% CPU
    STRESSED = "stressed"  # 80-90% CPU
    EXCITED = "excited"  # 90%+ or spike


MOOD_COLORS: dict[Mood, tuple[float, float, float]] = {
    Mood.SLEEPING: (0.30, 0.35, 0.55),
    Mood.SLEEPY: (0.45, 0.50, 0.65),
    Mood.DROWSY: (0.55, 0.58, 0.70),
    Mood.RELAXED: (0.50, 0.78, 0.55),
    Mood.HAPPY: (0.40, 0.75, 0.40),
    Mood.FOCUSED: (0.35, 0.60, 0.80),
    Mood.BUSY: (0.90, 0.65, 0.25),
    Mood.STRESSED: (0.90, 0.45, 0.20),
    Mood.EXCITED: (0.90, 0.30, 0.30),
}

_MOOD_LABELS: dict[Mood, str] = {
    Mood.SLEEPING: _("Sleeping"),
    Mood.SLEEPY: _("Sleepy"),
    Mood.DROWSY: _("Drowsy"),
    Mood.RELAXED: _("Relaxed"),
    Mood.HAPPY: _("Happy"),
    Mood.FOCUSED: _("Focused"),
    Mood.BUSY: _("Busy"),
    Mood.STRESSED: _("Stressed"),
    Mood.EXCITED: _("Excited!"),
}


@dataclass(frozen=True, slots=True)
class PetState:
    mood: Mood = Mood.HAPPY
    cpu: float = 0.0
    smoothed_cpu: float = 0.0
    idle_ticks: int = 0
    pending_mood: Mood | None = None
    pending_count: int = 0


class TickResult(NamedTuple):
    state: PetState
    mood_changed: bool
    should_refresh: bool


def resolve_mood(cpu: float, prev_cpu: float, idle_ticks: int) -> Mood:
    """Determine target mood from current readings.

    CPU ranges:
        0-5%   idle (mood depends on duration)
        5-20%  relaxed
        20-40% happy
        40-60% focused
        60-80% busy
        80-90% stressed
        90%+   excited (also on spike >30%)
    """
    # Spike or extreme load → excited
    if cpu >= 0.90 or (cpu - prev_cpu) >= SPIKE_DELTA:
        return Mood.EXCITED
    if cpu >= VERY_BUSY_CPU:
        return Mood.STRESSED
    if cpu >= BUSY_CPU:
        return Mood.BUSY
    if cpu >= MODERATE_CPU:
        return Mood.FOCUSED
    if cpu >= RELAXED_CPU:
        return Mood.HAPPY
    # Below idle threshold — drowsy/sleepy/sleeping by duration
    if cpu < IDLE_CPU:
        if idle_ticks >= SLEEPING_TICKS:
            return Mood.SLEEPING
        if idle_ticks >= SLEEPY_TICKS:
            return Mood.SLEEPY
        if idle_ticks >= DROWSY_TICKS:
            return Mood.DROWSY
    return Mood.RELAXED


def tick(state: PetState, raw_cpu: float) -> TickResult:
    """Advance pet state by one tick with a new CPU reading."""
    prev_cpu = state.smoothed_cpu
    smoothed = prev_cpu + SMOOTH_ALPHA * (raw_cpu - prev_cpu)

    idle_ticks = state.idle_ticks + 1 if smoothed < IDLE_CPU else 0

    target = resolve_mood(
        cpu=smoothed,
        prev_cpu=prev_cpu,
        idle_ticks=idle_ticks,
    )

    # Hysteresis: only commit after N consecutive ticks targeting same mood
    mood = state.mood
    pending_mood = state.pending_mood
    pending_count = state.pending_count
    mood_changed = False

    if target != mood:
        if target == pending_mood:
            pending_count += 1
        else:
            pending_mood = target
            pending_count = 1
        if pending_count >= HYSTERESIS:
            mood = target
            mood_changed = True
            pending_mood = None
            pending_count = 0
    else:
        pending_mood = None
        pending_count = 0

    cpu_delta = abs(smoothed - state.cpu)
    should_refresh = mood_changed or cpu_delta >= CPU_REDRAW_THRESHOLD

    new_state = PetState(
        mood=mood,
        cpu=smoothed,
        smoothed_cpu=smoothed,
        idle_ticks=idle_ticks,
        pending_mood=pending_mood,
        pending_count=pending_count,
    )
    return TickResult(
        state=new_state,
        mood_changed=mood_changed,
        should_refresh=should_refresh,
    )


def reset_to_happy(state: PetState) -> PetState:
    """Reset mood to happy (user clicked the pet)."""
    return replace(state, mood=Mood.HAPPY, pending_mood=None, pending_count=0)


def mood_color(mood: Mood) -> tuple[float, float, float]:
    """Body RGB for the given mood."""
    return MOOD_COLORS[mood]


def mood_label(mood: Mood) -> str:
    """Human-readable mood name."""
    return _MOOD_LABELS.get(mood, str(mood))


def tooltip_text(mood: Mood, cpu: float) -> str:
    """Build tooltip string."""
    return _("{mood} | CPU: {cpu}%").format(
        mood=mood_label(mood=mood),
        cpu=f"{cpu * 100:.0f}",
    )


__all__ = [
    "CpuSample",
    "Mood",
    "PetState",
    "TickResult",
    "cpu_percent",
    "mood_color",
    "mood_label",
    "parse_proc_stat",
    "reset_to_happy",
    "resolve_mood",
    "tick",
    "tooltip_text",
]
