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

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from docking.applets.base import Applet
from docking.applets.desktop import meta
from docking.applets.desktop.render import create_icon
from docking.core.icons import IconSource
from docking.i18n import _

if TYPE_CHECKING:
    from docking.applets.services import AppletServices
    from docking.core.config import Config
    from docking.platform.backends.base import DesktopActionService


class DesktopApplet(Applet):
    """Click to toggle showing the desktop (minimize/restore all windows)."""

    id = meta.id
    name = _("Desktop")
    icon_name = "user-desktop"
    icon_source_options = (IconSource.DOCKING, IconSource.SYSTEM)

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._desktop_actions: DesktopActionService | None = None
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def set_services(self, services: AppletServices) -> None:
        self._desktop_actions = services.desktop_actions

    def create_docking_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(size=size)

    def on_clicked(self) -> None:
        """Toggle show desktop through the selected backend service."""
        if self._desktop_actions is None:
            return
        self._desktop_actions.show_desktop()
