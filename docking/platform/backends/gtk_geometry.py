"""GTK client-relative rectangles in the toolkit's logical desktop space."""

from __future__ import annotations

from docking.platform.backends.base import Rect


def logical_screen_rectangle(window: object, rect: Rect) -> Rect | None:
    native = window.get_window()
    if native is None or not window.get_mapped():
        return None
    success, x, y = native.get_origin()
    if not success:
        return None
    return Rect(x + rect.x, y + rect.y, rect.width, rect.height)
