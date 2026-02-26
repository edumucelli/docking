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
