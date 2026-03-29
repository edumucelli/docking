"""Runtime command surfaces exposed by the dock UI shell to handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

if TYPE_CHECKING:
    from docking.core.theme import Theme
    from docking.ui.dock_window import DockWindow


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


class DockRuntime:
    """Narrow imperative API for subsystems that should not own DockWindow."""

    def __init__(self, window: DockWindow) -> None:
        self._window = window

    def menu_popup_opened(self) -> None:
        self._window.interaction.menu_popup_opened()

    def menu_popup_closed(self) -> None:
        self._window.interaction.menu_popup_closed()

    def on_hide_mode_changed(self) -> None:
        self._window.on_hide_mode_changed()

    def get_monitor_menu_choices(self) -> list[tuple[str, int]]:
        return self._window.placement.get_monitor_menu_choices()

    def current_monitor_choice(self) -> int:
        return self._window.placement.current_monitor_choice()

    def primary_monitor_index(self) -> int:
        return self._window.placement.primary_monitor_index()

    def reposition(self) -> None:
        self._window.placement.reposition()

    def set_active_display(self, enabled: bool) -> None:
        if enabled:
            self._window.placement.start_active_display()
        else:
            self._window.placement.stop_active_display()

    def set_icons_locked(self, locked: bool) -> None:
        self._window.dnd.set_locked(locked)

    def queue_draw(self) -> None:
        self._window.queue_redraw()

    def hide_tooltip(self) -> None:
        self._window.tooltip.hide()

    def hide_hover_ui(self) -> None:
        self._window.tooltip.hide()
        self._window.preview.hide()

    def set_theme(self, theme: Theme) -> None:
        self._window.theme = theme

    def cursor_position(self) -> tuple[float, float]:
        return self._window.cursor_x, self._window.cursor_y
