"""Dock position types and helpers."""

from __future__ import annotations

import enum


class Position(str, enum.Enum):
    BOTTOM = "bottom"
    TOP = "top"
    LEFT = "left"
    RIGHT = "right"


def is_horizontal(pos: Position) -> bool:
    """True for bottom/top (icons laid out left-to-right)."""
    return pos in (Position.BOTTOM, Position.TOP)
