"""Display and screen-space helpers shared across UI surfaces."""

from __future__ import annotations

import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk


def get_pointer_position(display: Gdk.Display) -> tuple[int, int] | None:
    """Return (x, y) screen coordinates of the pointer, or *None* if unavailable."""
    seat = display.get_default_seat()
    if not seat:
        return None
    pointer = seat.get_pointer()
    if not pointer:
        return None
    try:
        _, screen_x, screen_y = pointer.get_position()
    except Exception:
        return None
    return int(screen_x), int(screen_y)


def clamp_to_screen(
    rect_x: int,
    rect_y: int,
    rect_w: int,
    rect_h: int,
    screen_w: int,
    screen_h: int,
) -> tuple[int, int]:
    """Clamp a rectangle so it stays within screen bounds."""
    return (
        max(0, min(rect_x, screen_w - rect_w)),
        max(0, min(rect_y, screen_h - rect_h)),
    )
