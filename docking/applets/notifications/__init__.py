"""Notifications applet package."""

from .applet import NotificationsApplet
from .render import create_notifications_icon
from .state import (
    DunstBackend,
    GnomeBackend,
    NotificationsState,
    NullBackend,
    detect_backend,
    tooltip_text,
    unavailable_state,
)

__all__ = [
    "DunstBackend",
    "GnomeBackend",
    "NotificationsApplet",
    "NotificationsState",
    "NullBackend",
    "create_notifications_icon",
    "detect_backend",
    "tooltip_text",
    "unavailable_state",
]
