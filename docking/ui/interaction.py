"""Dock interaction policy shared across event handlers and menu/runtime hooks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from docking.log import get_logger
from docking.ui.geometry import current_input_rect, point_inside_input_rect

if TYPE_CHECKING:
    from gi.repository import Gtk

    from docking.ui.dock_window import DockWindow

_log = get_logger(name="interaction")


def should_keep_cursor_on_leave(
    *, autohide_enabled: bool, preview_visible: bool
) -> bool:
    """Whether leave handling should preserve cursor/hover identity."""
    return autohide_enabled or preview_visible


class DockInteractionCoordinator:
    """Owns dock-hover state and effective enter/leave policy."""

    def __init__(self, window: DockWindow) -> None:
        self._window = window

    @property
    def dock_hovered(self) -> bool:
        return self._window.dock_hovered

    @dock_hovered.setter
    def dock_hovered(self, value: bool) -> None:
        self._window.dock_hovered = value

    def menu_popup_opened(self) -> None:
        """Track that a dock context menu popup is currently active."""
        self._window._menu_popup_visible = True
        if self._window.autohide and self._window.autohide.enabled:
            self._window.autohide.set_disabled(True, reason="menu-open")

    def menu_popup_closed(self) -> None:
        """Reconcile autohide state when the context menu closes."""
        if not self._window._menu_popup_visible:
            return
        self._window._menu_popup_visible = False

        if not self._window.autohide or not self._window.autohide.enabled:
            return
        pointer_inside = self.pointer_inside_input_rect()
        if pointer_inside:
            self._window.autohide.set_hovered(True)
            self._window.autohide.set_disabled(
                False, reason="menu-close-pointer-inside"
            )
            return

        self._window._hover.hovered_item = None
        self._window._hover.cancel()
        self._window.tooltip.hide()

        preview_visible = bool(
            self._window.preview and self._window.preview.get_visible()
        )
        if self._window.preview and preview_visible:
            self._window.preview.schedule_hide()

        self._window.update_dock_size()
        self._window.drawing_area.queue_draw()
        self._window.autohide.set_hovered(False)
        self._window.autohide.set_disabled(False, reason="menu-close-pointer-outside")
        if not preview_visible:
            self._window.autohide.on_mouse_leave()

    def pointer_inside_input_rect(self) -> bool:
        """Return True when pointer is inside current dock input region."""
        frame = (
            self._window._current_geometry_frame or self._window._applied_input_frame
        )
        input_rect = current_input_rect(frame)
        if input_rect is None or not self._window.get_realized():
            return False
        display = self._window.get_display()
        if not display:
            return False
        seat = display.get_default_seat()
        if not seat:
            return False
        pointer = seat.get_pointer()
        if not pointer:
            return False
        try:
            _, screen_x, screen_y = pointer.get_position()
            win_x, win_y = self._window.get_position()
        except Exception:
            return False

        local_x = screen_x - win_x
        local_y = screen_y - win_y
        return point_inside_input_rect(frame, x=local_x, y=local_y)

    def on_effective_enter(self) -> None:
        if self._window.dock_hovered:
            return
        self._window.dock_hovered = True
        if self._window.autohide:
            self._window.autohide.on_mouse_enter()

    def on_effective_leave(self, widget: Gtk.DrawingArea) -> None:
        preview_visible = bool(
            self._window.preview and self._window.preview.get_visible()
        )
        if self._window.preview and preview_visible:
            self._window.preview.schedule_hide()

        autohide_on = bool(self._window.autohide and self._window.autohide.enabled)
        hovered_before = (
            self._window._hover.hovered_item.desktop_id
            if self._window._hover.hovered_item
            else "-"
        )
        keep_cursor = should_keep_cursor_on_leave(
            autohide_enabled=autohide_on,
            preview_visible=preview_visible,
        )
        self._window.dock_hovered = False
        if not keep_cursor:
            self._window._hover.hovered_item = None
            self._window.cursor_x = -1.0
            self._window.cursor_y = -1.0

        _log.debug(
            (
                "leave-policy: hovered_before=%s keep_cursor=%s "
                "preview_visible=%s autohide=%s hovered_after=%s "
                "cursor=(%.0f,%.0f)"
            ),
            hovered_before,
            keep_cursor,
            preview_visible,
            autohide_on,
            (
                self._window._hover.hovered_item.desktop_id
                if self._window._hover.hovered_item
                else "-"
            ),
            self._window.cursor_x,
            self._window.cursor_y,
        )

        self._window._hover.cancel()
        self._window.tooltip.hide()
        self._window.update_dock_size()
        widget.queue_draw()
        if autohide_on and self._window.autohide and not preview_visible:
            self._window.autohide.on_mouse_leave()

    def is_pointer_inside_dock(self) -> bool:
        """Return True when the current pointer is inside the dock input area."""
        return self.pointer_inside_input_rect()

    def point_inside_event_frame(self, *, x: float, y: float) -> bool:
        frame = (
            self._window._current_geometry_frame or self._window._applied_input_frame
        )
        input_rect = current_input_rect(frame)
        if input_rect is None:
            return False
        return input_rect.contains(x, y)
