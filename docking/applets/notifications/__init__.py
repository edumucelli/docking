"""Public package surface for the Notifications applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``NotificationsApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from __future__ import annotations

from docking.applets.identity import AppletCategory, AppletMeta

meta = AppletMeta(
    id="notifications",
    name="Notifications",
    category=AppletCategory.SYSTEM,
)

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
    "meta",
    "tooltip_text",
    "unavailable_state",
]
