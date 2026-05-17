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

"""Display and screen-space helpers shared across UI surfaces."""

from __future__ import annotations

from typing import NamedTuple

import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

from docking.log import get_logger

log = get_logger("display")


class ScreenPosition(NamedTuple):
    """Screen-space coordinates."""

    x: int
    y: int


def get_pointer_position(display: Gdk.Display) -> ScreenPosition | None:
    """Return screen coordinates of the pointer, or *None* if unavailable."""
    seat = display.get_default_seat()
    if not seat:
        return None
    pointer = seat.get_pointer()
    if not pointer:
        return None
    try:
        _, screen_x, screen_y = pointer.get_position()
    except Exception as exc:
        log.debug("Failed to query pointer position: %s", exc)
        return None
    return ScreenPosition(x=int(screen_x), y=int(screen_y))


def clamp_to_screen(
    rect_x: int,
    rect_y: int,
    rect_w: int,
    rect_h: int,
    screen_w: int,
    screen_h: int,
) -> ScreenPosition:
    """Clamp a rectangle so it stays within screen bounds."""
    return ScreenPosition(
        x=max(0, min(rect_x, screen_w - rect_w)),
        y=max(0, min(rect_y, screen_h - rect_h)),
    )
