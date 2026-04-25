"""Pure state and data helpers for the Stretch Coach applet."""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from importlib import resources
from typing import Any

from docking.i18n import _
from docking.log import get_logger

DEFAULT_INTERVAL = 30
INTERVAL_PRESETS = (15, 30, 45, 60, 90)
REFRESH_EVERY_SECONDS = 10
_CARDS_RESOURCE = "stretch/cards.json"
log = get_logger("stretchcoach")


@dataclass(frozen=True, slots=True)
class StretchCard:
    title: str
    steps: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StretchCoachState:
    interval_min: int = DEFAULT_INTERVAL
    remaining: int = DEFAULT_INTERVAL * 60
    cards_enabled: bool = True
    due: bool = False
    active_card: StretchCard | None = None
    preview_card: StretchCard | None = None


@dataclass(frozen=True, slots=True)
class TickResult:
    state: StretchCoachState
    became_due: bool
    should_refresh: bool


FALLBACK_CARDS: tuple[StretchCard, ...] = (
    StretchCard(
        title="Neck Reset",
        steps=(
            "Drop your shoulders.",
            "Tilt your head left, then right.",
            "Take three slow breaths.",
        ),
    ),
    StretchCard(
        title="Desk Opener",
        steps=(
            "Stand up and clasp your hands behind your back.",
            "Lift your chest gently for 20 seconds.",
            "Relax and shake out your arms.",
        ),
    ),
    StretchCard(
        title="Wrist Release",
        steps=(
            "Extend one arm forward.",
            "Pull your fingers down lightly, then up.",
            "Repeat on the other side.",
        ),
    ),
)


def _parse_interval(value: object) -> int:
    try:
        if isinstance(value, bool | int | float):
            minutes = int(value)
        elif isinstance(value, str):
            minutes = int(value.strip())
        else:
            return DEFAULT_INTERVAL
    except (TypeError, ValueError) as exc:
        log.debug("Invalid stretch interval %r: %s", value, exc)
        return DEFAULT_INTERVAL
    return max(1, minutes)


def _parse_steps(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(step).strip() for step in raw if str(step).strip())


def _parse_card(raw: object) -> StretchCard | None:
    if not isinstance(raw, dict):
        return None
    raw_dict = {str(key): value for key, value in raw.items()}
    title = raw_dict.get("title")
    steps = _parse_steps(raw_dict.get("steps"))
    if not isinstance(title, str):
        return None
    title = title.strip()
    if not title or not steps:
        return None
    return StretchCard(title=title, steps=steps)


def _fallback_cards() -> list[StretchCard]:
    return list(FALLBACK_CARDS)


def load_cards() -> list[StretchCard]:
    """Load bundled offline stretch cards, falling back safely on failure."""
    try:
        card_ref = resources.files("docking.assets").joinpath(_CARDS_RESOURCE)
        with resources.as_file(card_ref) as path, path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (
        FileNotFoundError,
        ModuleNotFoundError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        log.warning("Failed to load stretch cards asset: %s", exc)
        return _fallback_cards()

    if not isinstance(payload, list):
        return _fallback_cards()

    cards = [card for entry in payload if (card := _parse_card(entry)) is not None]
    return cards or _fallback_cards()


def choose_random_card(
    cards: Sequence[StretchCard],
    chooser: Callable[[Sequence[StretchCard]], StretchCard] | None = None,
) -> StretchCard | None:
    if not cards:
        return None
    pick = chooser or random.choice
    return pick(cards)


def format_remaining(seconds: int) -> str:
    minutes = max(0, seconds) // 60
    secs = max(0, seconds) % 60
    return f"{minutes}:{secs:02d}"


def tooltip_text(state: StretchCoachState) -> str:
    lines: list[str]
    if state.due:
        lines = [_("Time to stretch!")]
        card = state.active_card
    else:
        lines = [
            _("Next stretch in {time}").format(time=format_remaining(state.remaining))
        ]
        card = state.preview_card

    if card is None:
        return lines[0]

    lines.extend(["", card.title])
    lines.extend(card.steps)
    return "\n".join(lines)


def state_from_prefs(prefs: Mapping[str, Any] | None) -> StretchCoachState:
    if not prefs:
        return StretchCoachState()
    interval = _parse_interval(prefs.get("interval", DEFAULT_INTERVAL))
    cards_enabled = bool(prefs.get("cards_enabled", True))
    return StretchCoachState(
        interval_min=interval,
        remaining=interval * 60,
        cards_enabled=cards_enabled,
    )


def prefs_from_state(state: StretchCoachState) -> dict[str, object]:
    return {
        "interval": state.interval_min,
        "cards_enabled": state.cards_enabled,
    }


def trigger_reminder(
    state: StretchCoachState,
    cards: Sequence[StretchCard],
    chooser: Callable[[Sequence[StretchCard]], StretchCard] | None = None,
) -> StretchCoachState:
    card = choose_random_card(cards, chooser=chooser) if state.cards_enabled else None
    return replace(
        state,
        remaining=0,
        due=True,
        active_card=card,
        preview_card=None,
    )


def acknowledge_reminder(state: StretchCoachState) -> StretchCoachState:
    return replace(
        state,
        remaining=state.interval_min * 60,
        due=False,
        active_card=None,
        preview_card=None,
    )


def show_preview_card(
    state: StretchCoachState,
    cards: Sequence[StretchCard],
    chooser: Callable[[Sequence[StretchCard]], StretchCard] | None = None,
) -> StretchCoachState:
    card = choose_random_card(cards, chooser=chooser)
    if state.due:
        return replace(state, active_card=card)
    return replace(state, preview_card=card)


def set_interval(state: StretchCoachState, minutes: int) -> StretchCoachState:
    interval = _parse_interval(minutes)
    if state.due:
        return replace(state, interval_min=interval)
    return replace(state, interval_min=interval, remaining=interval * 60)


def set_cards_enabled(state: StretchCoachState, enabled: bool) -> StretchCoachState:
    if enabled:
        return replace(state, cards_enabled=True)
    return replace(state, cards_enabled=False, active_card=None, preview_card=None)


def tick(
    state: StretchCoachState,
    cards: Sequence[StretchCard],
    chooser: Callable[[Sequence[StretchCard]], StretchCard] | None = None,
) -> TickResult:
    if state.due:
        return TickResult(state=state, became_due=False, should_refresh=False)

    remaining = max(0, state.remaining - 1)
    next_state = replace(state, remaining=remaining)
    if remaining == 0:
        due_state = trigger_reminder(next_state, cards=cards, chooser=chooser)
        return TickResult(state=due_state, became_due=True, should_refresh=True)

    should_refresh = remaining <= 10 or remaining % REFRESH_EVERY_SECONDS == 0
    return TickResult(
        state=next_state,
        became_due=False,
        should_refresh=should_refresh,
    )
