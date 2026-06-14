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

"""X11 desktop action service implementations."""

from __future__ import annotations

import gi

gi.require_version("Wnck", "3.0")
from gi.repository import Wnck

from docking.applets.desktop.state import next_showing_desktop
from docking.platform.backends.base import ActionResult, DesktopActionService


class WnckDesktopActionService(DesktopActionService):
    """DesktopActionService backed by Wnck show-desktop support."""

    def start(self) -> None:
        """No service-level runtime loop is needed."""

    def stop(self) -> None:
        """No persistent resources are held."""

    def show_desktop(self, show: bool | None = None) -> ActionResult:
        """Show, hide, or toggle desktop visibility through Wnck."""
        screen = Wnck.Screen.get_default()
        if screen is None:
            return ActionResult.NOT_FOUND
        screen.force_update()
        target = (
            next_showing_desktop(current=screen.get_showing_desktop())
            if show is None
            else bool(show)
        )
        screen.toggle_showing_desktop(target)
        return ActionResult.OK
