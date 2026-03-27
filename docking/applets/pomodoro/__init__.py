"""Public package surface for the Pomodoro applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``PomodoroApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="pomodoro",
    name="Pomodoro",
    category=AppletCategory.PRODUCTIVITY,
)

from .applet import PomodoroApplet
from .state import (
    DEFAULT_BREAK,
    DEFAULT_LONG_BREAK,
    DEFAULT_WORK,
    LONG_BREAK_EVERY,
    State,
    format_time,
    tooltip_text,
)

__all__ = [
    "DEFAULT_BREAK",
    "DEFAULT_LONG_BREAK",
    "DEFAULT_WORK",
    "LONG_BREAK_EVERY",
    "PomodoroApplet",
    "State",
    "format_time",
    "meta",
    "tooltip_text",
]
