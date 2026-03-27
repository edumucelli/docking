"""Public package surface for the Stretch Coach applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``StretchCoachApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="stretchcoach",
    name="Stretch Coach",
    category=AppletCategory.WELLNESS,
)

from .applet import StretchCoachApplet
from .state import (
    DEFAULT_INTERVAL,
    INTERVAL_PRESETS,
    StretchCard,
    StretchCoachState,
    TickResult,
    acknowledge_reminder,
    choose_random_card,
    load_cards,
    prefs_from_state,
    set_cards_enabled,
    set_interval,
    show_preview_card,
    state_from_prefs,
    tick,
    tooltip_text,
    trigger_reminder,
)

__all__ = [
    "DEFAULT_INTERVAL",
    "INTERVAL_PRESETS",
    "StretchCard",
    "StretchCoachApplet",
    "StretchCoachState",
    "TickResult",
    "acknowledge_reminder",
    "choose_random_card",
    "load_cards",
    "meta",
    "prefs_from_state",
    "set_cards_enabled",
    "set_interval",
    "show_preview_card",
    "state_from_prefs",
    "tick",
    "tooltip_text",
    "trigger_reminder",
]
