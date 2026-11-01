# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Session applet behavior and GTK wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

from docking.applets.platform import AppletPlatformServices

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from docking.applets.base import PlatformServicesApplet
from docking.applets.menu import menu_sections
from docking.applets.session import meta
from docking.core.icons import IconSource
from docking.i18n import _

from .render import create_session_icon
from .state import _ACTIONS

if TYPE_CHECKING:
    from docking.core.config import Config


class SessionApplet(PlatformServicesApplet):
    """Provides session and power management actions."""

    id = meta.id
    name = _("Session")
    icon_name = "system-log-out"
    icon_source_options = (IconSource.DOCKING, IconSource.SYSTEM)

    def __init__(
        self,
        icon_size: int,
        config: Config,
        *,
        platform_services: AppletPlatformServices,
    ) -> None:
        self._platform = platform_services
        super().__init__(icon_size, config, platform_services=platform_services)
        self._backend = self._platform.backends.session()
        self.present()

    def create_docking_icon(self, size: int):
        return create_session_icon(size=size)

    def on_clicked(self) -> None:
        """Lock screen on left-click."""
        self._backend.perform("lock")

    def stop(self) -> None:
        self._backend.stop()
        super().stop()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        primary: list[Gtk.MenuItem] = []
        destructive: list[Gtk.MenuItem] = []
        available = self._backend.available_actions()
        for label, action in _ACTIONS:
            mi = Gtk.MenuItem(label=label)
            mi.set_sensitive(action in available)
            mi.connect("activate", lambda _w, a=action: self._backend.perform(a))
            if action in ("lock", "suspend"):
                primary.append(mi)
            else:
                destructive.append(mi)
        return menu_sections(primary=primary, destructive=destructive, gtk=Gtk)
