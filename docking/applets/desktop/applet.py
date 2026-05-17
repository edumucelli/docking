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

"""GTK lifecycle glue for Desktop applet."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Wnck", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Wnck

from docking.applets.base import Applet
from docking.applets.desktop import meta
from docking.applets.desktop.render import create_icon
from docking.applets.desktop.state import next_showing_desktop
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config


class DesktopApplet(Applet):
    """Click to toggle showing the desktop (minimize/restore all windows)."""

    id = meta.id
    name = _("Desktop")
    icon_name = "user-desktop"
    supports_system_icon = True

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_docking_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(size=size)

    def on_clicked(self) -> None:
        """Toggle show desktop via Wnck."""
        screen = Wnck.Screen.get_default()
        screen.force_update()
        screen.toggle_showing_desktop(
            next_showing_desktop(current=screen.get_showing_desktop())
        )
