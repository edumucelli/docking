"""GTK Win32 placement, shell reservation and transparent-edge pointer fallback."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, cast

from gi.repository import GLib

from docking.core.position import Position
from docking.platform.backends.base import (
    DisplayServer,
    MonitorSnapshot,
    PlacementRequest,
    Rect,
    ReservationRequest,
    SurfaceService,
    WindowId,
    WindowSnapshot,
)
from docking.platform.backends.gtk_surface import apply_dock_hints, place_window

if TYPE_CHECKING:
    from .appbar import WindowsAppBar
    from .drag_source import WindowsFileDragSource
    from .geometry import ScreenMapping
    from .menus import WindowsMenuSession
    from .workspaces import WindowsWorkspaceService


class _CursorCoordinates(Protocol):
    cursor_x: float
    cursor_y: float


class WindowsSurfaceService(SurfaceService):
    """Share GTK placement while keeping Windows surface details in one place."""

    def __init__(self, *, workspaces: WindowsWorkspaceService | None = None) -> None:
        self._window: object | None = None
        self._workspaces = workspaces
        self._current_workspace_only = False
        self._appbar: WindowsAppBar | None = None
        self._reservation: ReservationRequest | None = None
        self._mapping: ScreenMapping | None = None
        self._changed: Callable[[], None] | None = None
        self._refresh_source = 0
        self._pointer_source = 0
        self._fullscreen_source = 0
        self._input_rect: Rect | None = None

        self._map_source = 0
        self._map_handler = 0
        self._placement: PlacementRequest | None = None
        self._menus: WindowsMenuSession | None = None
        self._file_drags: WindowsFileDragSource | None = None

    def screen_rectangle(self, window: object, rect: Rect) -> Rect | None:
        from docking.platform.backends.gtk_geometry import logical_screen_rectangle

        logical = logical_screen_rectangle(window, rect)
        if logical is None or self._appbar is None:
            return None
        display = window.get_display()
        monitor = display.get_monitor_at_window(window.get_window())
        if monitor is None:
            return None
        index = next(
            (
                i
                for i in range(display.get_n_monitors())
                if display.get_monitor(i) == monitor
            ),
            -1,
        )
        geometry = monitor.get_geometry()
        snapshot = MonitorSnapshot(
            index=index,
            geometry=Rect(geometry.x, geometry.y, geometry.width, geometry.height),
            scale=monitor.get_scale_factor(),
        )
        return self._appbar.mapping(snapshot, display).to_physical(logical)

    def start(self) -> None:
        """Native surface resources are attached when the window is realized."""

    def configure_before_realize(self, window: object) -> None:
        self._window = window
        apply_dock_hints(window)

    def on_realize(self, window: object) -> None:
        self._window = window
        apply_dock_hints(window)
        if self._appbar is None:
            from .appbar import WindowsAppBar

            self._appbar = WindowsAppBar(
                self._schedule_refresh, self._fullscreen_changed
            )

        if not self._map_handler:
            self._map_handler = window.connect("map-event", self._mapped)
        if self._menus is None:
            from .menus import WindowsMenuSession

            self._menus = WindowsMenuSession(window)
        self._menus.bind_parent(window.get_window())

    def prepare_menu_popup(self, menu: object) -> None:
        if self._window is None:
            return
        if self._menus is None:
            from .menus import WindowsMenuSession

            self._menus = WindowsMenuSession(self._window)
        self._menus.prepare(menu)

    def prepare_file_drag_source(
        self,
        widget: object,
        *,
        application_id: str,
        data: tuple[tuple[str, bytes], ...],
    ) -> bool:
        if self._window is None or not data:
            return False
        if self._file_drags is None:
            from .drag_source import WindowsFileDragSource

            self._file_drags = WindowsFileDragSource(self._window)
        self._file_drags.prepare(widget, application_id=application_id, data=data)
        return True

    def capture_source_window(self) -> WindowSnapshot | None:
        source = self._menus.capture_source() if self._menus is not None else None
        return (
            WindowSnapshot(
                id=WindowId(DisplayServer.WINDOWS, source[0]),
                desktop_id="",
                pid=source[1],
            )
            if source
            else None
        )

    def prepare_capture_overlay(self, overlay: object) -> None:
        if self._window is None:
            return
        if self._menus is None:
            from .menus import WindowsMenuSession

            self._menus = WindowsMenuSession(self._window)
        self._menus.prepare_capture(overlay)
        overlay.present()

    def stop(self) -> None:
        if self._file_drags is not None:
            self._file_drags.stop()
            self._file_drags = None
        if self._menus is not None:
            self._menus.stop()
            self._menus = None
        self._changed = None
        self._reservation = None
        if self._pointer_source:
            GLib.source_remove(self._pointer_source)
            self._pointer_source = 0
        if self._fullscreen_source:
            GLib.source_remove(self._fullscreen_source)
            self._fullscreen_source = 0
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

    def monitor_workarea(self, monitor: MonitorSnapshot) -> Rect | None:
        """Read live native reservations instead of GTK's cached work area."""
        if self._appbar is not None and self._window is not None:
            try:
                mapping = self._appbar.mapping(monitor, self._window.get_display())
            except OSError:
                # A display can disappear between toolkit selection and the
                # native query. Let the caller use its toolkit fallback.
                return None
            return mapping.to_logical(mapping.workarea)
        return super().monitor_workarea(monitor)

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
        if self._window is not None:
            place_window(self._window, request)

    def _mapped(self, *_args: object) -> bool:
        # Win32 initially constrains normal GTK windows to the work area. The
        # animation surface extends over its own reserved strip, so restore
        # its requested geometry after GTK finishes mapping the window.
        if not self._map_source:
            self._map_source = GLib.idle_add(
                self._restore_placement, priority=GLib.PRIORITY_LOW
            )
        self.set_workspace_scope(current_workspace_only=self._current_workspace_only)
        return False

    def set_workspace_scope(self, *, current_workspace_only: bool) -> None:
        self._current_workspace_only = current_workspace_only
        if (
            self._workspaces is None
            or not self._workspaces.supported
            or self._window is None
        ):
            return
        native = self._window.get_window()
        if native is not None:
            from .ffi import window_handle

            hwnd = window_handle(native)
            if hwnd:
                self._workspaces.set_surface_scope(
                    hwnd, current_workspace_only=current_workspace_only
                )

    def _restore_placement(self) -> bool:
        self._map_source = 0
        if self._window is not None and self._placement is not None:
            native = self._window.get_window()
            request = self._placement
            if native is not None:
                native.move_resize(
                    request.x, request.y, request.size.width, request.size.height
                )
                if (
                    self._appbar is not None
                    and self._mapping is not None
                    and self._reservation is not None
                ):
                    fullscreen = self._appbar.fullscreen_active(self._mapping)
                    if fullscreen is not None:
                        self._fullscreen_changed(fullscreen)
        return False

    def set_reservation(self, request: ReservationRequest) -> None:
        self._reservation = request
        if self._appbar is not None and self._window is not None:
            mapping = self._appbar.mapping(request.monitor, self._window.get_display())
            self._appbar.reserve(mapping, Position(request.position), request.thickness)

    def clear_reservation(self) -> None:
        self._reservation = None
        if self._fullscreen_source:
            self._fullscreen_changed(False)
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
            if not fullscreen and self._fullscreen_source:
                # Changing native z-order can deliver another shell callback
                # synchronously. Retire this observer before GTK so a nested
                # fullscreen notification can own a new one.
                GLib.source_remove(self._fullscreen_source)
                self._fullscreen_source = 0
            self._window.set_keep_above(not fullscreen)
            if fullscreen:
                # Removing TOPMOST places the dock at the top of the normal
                # band, still over the fullscreen application. AppBars must
                # move to the bottom of the z-order until fullscreen ends.
                native = self._window.get_window()
                if native is not None:
                    native.lower()
                if (
                    self._appbar is not None
                    and self._mapping is not None
                    and not self._fullscreen_source
                ):
                    # A window already fullscreen at AppBar registration may
                    # produce neither its opening nor its closing notification.
                    self._fullscreen_source = GLib.timeout_add(
                        250, self._poll_fullscreen
                    )

    def _poll_fullscreen(self) -> bool:
        if self._appbar is None or self._mapping is None:
            self._fullscreen_source = 0
            return False
        if self._appbar.fullscreen_active(self._mapping) is False:
            self._fullscreen_changed(False)
            return False
        return True

    def window_geometry(self, physical: Rect) -> Rect | None:
        """Project a foreign window into this dock monitor's GTK coordinates."""
        return self._mapping.to_logical(physical) if self._mapping is not None else None

    @property
    def input_region_alpha(self) -> float:
        # Win32 layered-window hit testing ignores zero-alpha pixels, including
        # holes inside an icon. One alpha byte keeps the shared input rectangle
        # interactive while the rest of the animation surface stays click-through.
        return 1.0 / 255.0

    def update_input_region(self, rect: Rect) -> None:
        # GDK Win32 input_shape_combine_region is a no-op. Layered windows use
        # pixel alpha for normal hit testing; retain shared geometry to reveal
        # the fully transparent hidden edge without intercepting clicks.
        self._input_rect = rect

    def set_blur_region(self, rect: Rect | None) -> None:
        """No native blur hint is applied to the Windows dock surface."""

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
