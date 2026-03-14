"""Session applet behavior and GTK wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from docking.applets.base import Applet
from docking.applets.identity import AppletId
from docking.i18n import _

from .render import create_session_icon
from .state import _ACTIONS, _run

if TYPE_CHECKING:
    from docking.core.config import Config


class SessionApplet(Applet):
    """Provides session and power management actions."""

    id = AppletId.SESSION
    name = _("Session")
    icon_name = "system-log-out"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        super().__init__(icon_size, config)

    def create_icon(self, size: int):
        return create_session_icon(size=size)

    def on_clicked(self) -> None:
        """Lock screen on left-click."""
        _run(cmd=["loginctl", "lock-session"], action="lock_screen")

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []
        for label, cmd in _ACTIONS:
            mi = Gtk.MenuItem(label=label)
            action = label.lower().replace(" ", "_")
            mi.connect("activate", lambda _w, c=cmd, a=action: _run(cmd=c, action=a))
            items.append(mi)
        return items
