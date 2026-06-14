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

"""X11 dock surface integration."""

from __future__ import annotations

from collections.abc import Callable

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11

from docking.core.position import Position
from docking.platform.backends.base import (
    MonitorSnapshot,
    PlacementRequest,
    Rect,
    ReservationRequest,
    SurfaceService,
)
from docking.platform.backends.x11.impl.barriers import PointerBarrier
from docking.platform.backends.x11.impl.struts import (
    BlurRect,
    clear_blur_region,
    clear_struts,
    compute_blur_region,
    set_blur_region,
    set_dock_struts,
)


class X11SurfaceService(SurfaceService):
    """SurfaceService implementation for the current X11 dock window."""

    def __init__(self, *, barrier: PointerBarrier | None = None) -> None:
        self._window: object | None = None
        self._barrier = barrier if barrier is not None else PointerBarrier()

    def start(self) -> None:
        """No service-level runtime loop is needed."""

    def stop(self) -> None:
        """Release X11 pointer-barrier resources."""
        self._barrier.shutdown()

    def configure_before_realize(self, window: object) -> None:
        """Configure the GTK window as an X11 dock before realization."""
        self._window = window
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        window.stick()
        window.set_keep_above(True)
        window.set_type_hint(Gdk.WindowTypeHint.DOCK)

    def on_realize(self, window: object) -> None:
        """Initialize X11-only surface helpers after realization."""
        self._window = window
        display = window.get_display()
        if display and isinstance(display, GdkX11.X11Display):
            self._barrier.initialize(gdk_display=display)

    def set_workspace_scope(self, *, current_workspace_only: bool) -> None:
        """Apply X11 workspace stickiness for the dock window."""
        if self._window is None:
            return
        if current_workspace_only:
            self._window.unstick()
        else:
            self._window.stick()

    def position_or_anchor(self, request: PlacementRequest) -> None:
        """Move and resize the X11 dock window."""
        if self._window is None:
            return
        self._window.set_size_request(request.size.width, request.size.height)
        self._window.resize(request.size.width, request.size.height)
        self._window.move(request.x, request.y)

    def set_reservation(self, request: ReservationRequest) -> None:
        """Publish X11 struts for an always-visible dock."""
        window = self._window
        if window is None:
            return
        gdk_window = window.get_window()
        if not gdk_window or not isinstance(gdk_window, GdkX11.X11Window):
            return
        screen = window.get_screen()
        if screen is None:
            return
        geom = request.monitor.geometry
        if not isinstance(request.position, Position):
            return
        monitor_geom = Gdk.Rectangle()
        monitor_geom.x = geom.x
        monitor_geom.y = geom.y
        monitor_geom.width = geom.width
        monitor_geom.height = geom.height
        set_dock_struts(
            gdk_window=gdk_window,
            dock_height=request.thickness,
            monitor_geom=monitor_geom,
            screen=screen,
            position=request.position,
        )

    def clear_reservation(self) -> None:
        """Clear X11 struts from the dock window."""
        window = self._window
        if window is None:
            return
        gdk_window = window.get_window()
        if not gdk_window or not isinstance(gdk_window, GdkX11.X11Window):
            return
        clear_struts(gdk_window=gdk_window)

    def update_pointer_barrier(
        self,
        *,
        monitor: MonitorSnapshot | None,
        position: object,
        enabled: bool,
        pressure_callback: Callable[[], None] | None = None,
        pressure_threshold: int = 1,
    ) -> None:
        """Create, update, or destroy the X11 pointer barrier."""
        if not self._barrier.supported:
            return
        if not enabled or monitor is None:
            self._barrier.destroy()
            return
        if not isinstance(position, Position):
            return
        geom = monitor.geometry
        self._barrier.update(
            position=position,
            monitor_x=geom.x,
            monitor_y=geom.y,
            monitor_w=geom.width,
            monitor_h=geom.height,
            scale=monitor.scale,
        )
        if callable(pressure_callback):
            self._barrier.set_pressure_handler(
                callback=pressure_callback,
                threshold=pressure_threshold,
            )
        else:
            self._barrier.set_pressure_handler(callback=None, threshold=1)

    def update_input_region(self, rect: Rect) -> None:
        """Apply the X11 input shape for the active dock area."""
        window = self._window
        if window is None:
            return
        gdk_window = window.get_window()
        if not gdk_window:
            return
        region = cairo.Region(
            cairo.RectangleInt(rect.x, rect.y, rect.width, rect.height)
        )
        gdk_window.input_shape_combine_region(region, 0, 0)

    def set_blur_region(self, rect: Rect | None) -> None:
        """Set or clear the X11 compositor blur hint."""
        window = self._window
        if window is None:
            return
        gdk_window = window.get_window()
        if not gdk_window or not isinstance(gdk_window, GdkX11.X11Window):
            return
        if rect is None:
            clear_blur_region(gdk_window=gdk_window)
            return
        theme = window.theme
        config = window.config
        blur_region = compute_blur_region(
            rect=BlurRect(
                x=rect.x,
                y=rect.y,
                width=rect.width,
                height=rect.height,
            ),
            roundness=theme.roundness,
            round_bottom=theme.round_bottom,
            position=config.pos,
            scale=gdk_window.get_scale_factor(),
        )
        set_blur_region(gdk_window=gdk_window, blur_region=blur_region)
