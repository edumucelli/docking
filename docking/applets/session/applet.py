"""Session applet behavior and GTK wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from docking.applets.base import Applet
from docking.applets.menu import menu_sections
from docking.applets.session import meta
from docking.i18n import _

from .render import create_session_icon
from .state import _ACTIONS, LOCK_SCREEN_LABEL, _run, lock_screen

if TYPE_CHECKING:
    from docking.core.config import Config


class SessionApplet(Applet):
    """Provides session and power management actions."""

    id = meta.id
    name = _("Session")
    icon_name = "system-log-out"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        super().__init__(icon_size, config)
        self.present()

    def create_icon(self, size: int):
        return create_session_icon(size=size)

    def on_clicked(self) -> None:
        """Lock screen on left-click."""
        lock_screen()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        primary: list[Gtk.MenuItem] = []
        destructive: list[Gtk.MenuItem] = []
        for label, cmd in _ACTIONS:
            mi = Gtk.MenuItem(label=label)
            action = label.lower().replace(" ", "_")
            if label == LOCK_SCREEN_LABEL:
                mi.connect("activate", lambda _w: lock_screen())
            else:
                mi.connect(
                    "activate",
                    lambda _w, c=cmd, a=action: _run(cmd=c, action=a),
                )
            if label in (LOCK_SCREEN_LABEL, _("Suspend")):
                primary.append(mi)
            else:
                destructive.append(mi)
        return menu_sections(primary=primary, destructive=destructive, gtk=Gtk)
