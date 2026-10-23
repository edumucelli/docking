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

"""X11 overlap/dodge visibility service."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from docking.platform.backends.base import Rect, VisibilityMonitor, VisibilityService
from docking.platform.backends.x11.impl.dodge import ScreenRect, WindowDodgeMonitor

if TYPE_CHECKING:
    from docking.core.config import Config


class X11VisibilityService(VisibilityService):
    """VisibilityService backed by the existing Wnck WindowDodgeMonitor."""

    def __init__(self, *, config: Config) -> None:
        self._config = config

    def start(self) -> None:
        """No service-level runtime loop is needed."""

    def stop(self) -> None:
        """No service-level resources are held."""

    def create_monitor(
        self,
        *,
        get_dock_rect: Callable[[], Rect | None],
        on_change: Callable[[bool], None],
    ) -> VisibilityMonitor | None:
        """Create the X11 dodge monitor used for overlap-based hiding."""
        return WindowDodgeMonitor(
            config=self._config,
            get_dock_rect=lambda: _to_screen_rect(get_dock_rect()),
            on_change=on_change,
        )


def _to_screen_rect(rect: Rect | None) -> ScreenRect | None:
    if rect is None:
        return None
    return ScreenRect(x=rect.x, y=rect.y, width=rect.width, height=rect.height)
