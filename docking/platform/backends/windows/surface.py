"""GTK Win32 placement, shell reservation and transparent-edge pointer fallback."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, cast

from gi.repository import GLib

from docking.core.position import Position
from docking.platform.backends.base import (
    MonitorSnapshot,
    PlacementRequest,
    Rect,
    ReservationRequest,
)
from docking.platform.backends.reduced.services import ReducedSurfaceService

if TYPE_CHECKING:
    from .appbar import ScreenMapping, WindowsAppBar


class _CursorCoordinates(Protocol):
    cursor_x: float
    cursor_y: float


class WindowsSurfaceService(ReducedSurfaceService):
    """Share GTK placement while keeping Windows surface details in one place."""

    def __init__(self) -> None:
        super().__init__()
        self._appbar: WindowsAppBar | None = None
        self._reservation: ReservationRequest | None = None
        self._mapping: ScreenMapping | None = None
        self._changed: Callable[[], None] | None = None
        self._refresh_source = 0
        self._pointer_source = 0
        self._input_rect: Rect | None = None

        self._map_source = 0
        self._map_handler = 0
        self._placement: PlacementRequest | None = None

    def on_realize(self, window: object) -> None:
        super().on_realize(window)
        if self._appbar is None:
            from .appbar import WindowsAppBar

            self._appbar = WindowsAppBar(
                self._schedule_refresh, self._fullscreen_changed
            )

        if not self._map_handler:
            self._map_handler = window.connect("map-event", self._mapped)

    def stop(self) -> None:
        self._changed = None
        self._reservation = None
        if self._pointer_source:
            GLib.source_remove(self._pointer_source)
            self._pointer_source = 0
        if self._map_source:
            GLib.source_remove(self._map_source)
            self._map_source = 0
        if self._map_handler and self._window is not None:
            if self._window.handler_is_connected(self._map_handler):
                self._window.disconnect(self._map_handler)
            self._map_handler = 0
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = 0
        if self._appbar is not None:
            self._appbar.close()
            self._appbar = None
        self._mapping = None

    def external_workarea(self, monitor: MonitorSnapshot) -> Rect:
        """Keep every dock edge inside Explorer's available desktop area."""
        if self._appbar is not None and self._window is not None:
            mapping = self._appbar.mapping(monitor, self._window.get_display())
            self._mapping = mapping
            position = self._window.config.pos
            return mapping.to_logical(self._appbar.external(mapping, position))
        return monitor.workarea or monitor.geometry

    def position_or_anchor(self, request: PlacementRequest) -> None:
        self._placement = request
        if self._appbar is not None and self._window is not None:
            self._mapping = self._appbar.mapping(
                request.monitor, self._window.get_display()
            )
        super().position_or_anchor(request)

    def _mapped(self, *_args: object) -> bool:
        # Win32 initially constrains normal GTK windows to the work area. The
        # animation surface extends over its own reserved strip, so restore
        # its requested geometry after GTK finishes mapping the window.
        if not self._map_source:
            self._map_source = GLib.idle_add(
                self._restore_placement, priority=GLib.PRIORITY_LOW
            )
        return False

    def _restore_placement(self) -> bool:
        self._map_source = 0
        if self._window is not None and self._placement is not None:
            native = self._window.get_window()
            request = self._placement
            if native is not None:
                native.move_resize(
                    request.x, request.y, request.size.width, request.size.height
                )
        return False

    def set_reservation(self, request: ReservationRequest) -> None:
        self._reservation = request
        if self._appbar is not None and self._window is not None:
            mapping = self._appbar.mapping(request.monitor, self._window.get_display())
            self._appbar.reserve(mapping, Position(request.position), request.thickness)

    def clear_reservation(self) -> None:
        self._reservation = None
        if self._appbar is not None:
            self._appbar.clear()

    def set_external_workarea_changed_handler(
        self, callback: Callable[[], None] | None
    ) -> None:
        self._changed = callback

    def refresh_external_workarea(self) -> None:
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if not self._refresh_source:
            self._refresh_source = GLib.idle_add(self._refresh)

    def _refresh(self) -> bool:
        self._refresh_source = 0
        if self._reservation is not None:
            self.set_reservation(self._reservation)
        if self._changed is not None:
            self._changed()
        return False

    def _fullscreen_changed(self, fullscreen: bool) -> None:
        if self._window is not None:
            self._window.set_keep_above(not fullscreen)

    def window_geometry(self, physical: Rect) -> Rect | None:
        """Project a foreign window into this dock monitor's GTK coordinates."""
        return self._mapping.to_logical(physical) if self._mapping is not None else None

    def update_input_region(self, rect: Rect) -> None:
        # GDK Win32 input_shape_combine_region is a no-op. Layered windows use
        # pixel alpha for normal hit testing; retain shared geometry to reveal
        # the fully transparent hidden edge without intercepting clicks.
        self._input_rect = rect

    def update_pointer_barrier(
        self,
        *,
        monitor,
        position: object,
        enabled: bool,
        pressure_callback=None,
        pressure_threshold: int = 1,
    ) -> None:
        # This is an edge observer, not a confinement/pressure barrier.
        if enabled and not self._pointer_source:
            self._pointer_source = GLib.timeout_add(75, self._poll_pointer)
        elif not enabled and self._pointer_source:
            GLib.source_remove(self._pointer_source)
            self._pointer_source = 0

    def _poll_pointer(self) -> bool:
        window, mapping, placement = self._window, self._mapping, self._placement
        if (
            window is None
            or mapping is None
            or placement is None
            or self._appbar is None
        ):
            return True
        point, rect = self._appbar.cursor_position(), self._input_rect
        if point is None or rect is None:
            return True
        logical = mapping.to_logical(Rect(point[0], point[1], 1, 1))
        x, y = logical.x - placement.x, logical.y - placement.y
        inside = rect.x <= x < rect.right and rect.y <= y < rect.bottom
        if inside and not window.dock_hovered:
            cursor = cast(_CursorCoordinates, window)
            cursor.cursor_x, cursor.cursor_y = x, y
            window.interaction.on_effective_enter()
        elif not inside and window.dock_hovered:
            window.interaction.on_effective_leave(window.drawing_area)
        return True
