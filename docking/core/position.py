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

"""Dock position types and helpers."""

from __future__ import annotations

import enum


class Position(str, enum.Enum):
    """Screen edge where the dock is anchored.

    Coordinate convention:
      main axis  -- along the dock (horizontal for BOTTOM/TOP, vertical for LEFT/RIGHT)
      cross axis -- perpendicular to the dock (toward/away from screen edge)
    """

    BOTTOM = "bottom"
    TOP = "top"
    LEFT = "left"
    RIGHT = "right"


def is_horizontal(pos: Position) -> bool:
    """True for bottom/top (icons laid out left-to-right)."""
    return pos in (Position.BOTTOM, Position.TOP)
