"""GTK Win32 dock placement and native GDK input shaping."""

from __future__ import annotations

from docking.platform.backends.base import MonitorSnapshot, Rect
from docking.platform.backends.reduced.services import ReducedSurfaceService


class WindowsSurfaceService(ReducedSurfaceService):
    """Share GTK placement while keeping Windows surface details in one place."""

    def external_workarea(self, monitor: MonitorSnapshot) -> Rect:
        """Keep every dock edge inside Explorer's available desktop area."""
        return monitor.workarea or monitor.geometry

    def update_input_region(self, rect: Rect) -> None:
        window = self._window
        if window is None:
            return
        native = window.get_window()
        if native is None:
            return
        import cairo

        native.input_shape_combine_region(
            cairo.Region(
                cairo.RectangleInt(
                    rect.x,
                    rect.y,
                    max(0, rect.width),
                    max(0, rect.height),
                )
            ),
            0,
            0,
        )
