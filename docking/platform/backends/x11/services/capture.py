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

"""X11 screen capture service."""

from __future__ import annotations

from docking.platform.backends.base import ScreenCaptureService
from docking.platform.backends.x11.impl.screen_capture import pick_pixel


class X11ScreenCaptureService(ScreenCaptureService):
    """ScreenCaptureService backed by X11 root-window color sampling."""

    def start(self) -> None:
        """No service-level runtime loop is needed."""

    def stop(self) -> None:
        """No persistent resources are held."""

    def pick_color(self, *, x: int, y: int) -> tuple[int, int, int] | None:
        return pick_pixel(x=x, y=y)
