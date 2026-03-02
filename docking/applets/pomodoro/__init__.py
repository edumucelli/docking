"""Pomodoro applet public API."""

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
    "tooltip_text",
]
