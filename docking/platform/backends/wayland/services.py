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

"""Native Wayland surface services backed by gtk-layer-shell.

Backend selection
  |
  +-- X11 display -----------------> X11SessionBackend
  |
  +-- Wayland + layer-shell -------> WaylandLayerShellSessionBackend
  |                                    |
  |                                    +-- WaylandLayerShellSurfaceService
  |                                    +-- ForeignToplevelWindowService
  |                                    |     when protocol adapter is available
  |                                    +-- ReducedWindowService fallback
  |                                    +-- Reduced preview/visibility
  |
  +-- Wayland without layer-shell --> ReducedSessionBackend

Layer-shell changes the dock surface role and edge placement. Foreign-toplevel
support, when available, separately provides running and active app state.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import cairo

from docking.core.position import Position
from docking.platform.backends.base import (
    MonitorSnapshot,
    PlacementRequest,
    Rect,
    ReservationRequest,
    SurfaceService,
)


def load_gtk_layer_shell() -> object | None:
    """Return the optional GtkLayerShell GIR module, or None when unavailable."""
    try:
        import gi

        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import GtkLayerShell
    except (ImportError, ValueError):
        return None
    return GtkLayerShell


def layer_shell_is_supported(layer_shell: object) -> bool:
    """Return True when the loaded GtkLayerShell module can be used now."""
    is_supported = getattr(layer_shell, "is_supported", None)
    if not callable(is_supported):
        return True
    try:
        return bool(is_supported())
    except Exception:
        return False


class WaylandLayerShellSurfaceService(SurfaceService):
    """Layer-shell implementation for the main dock surface."""

    def __init__(
        self,
        *,
        layer_shell: object,
        on_layer_surface_ready: Callable[[object], None] | None = None,
    ) -> None:
        self._layer_shell = layer_shell
        self._window: object | None = None
        self._exclusive_zone: int = 0
        self._on_layer_surface_ready = on_layer_surface_ready

    def start(self) -> None:
        """No service-level runtime loop is needed."""

    def stop(self) -> None:
        """No service-level resources are held."""

    def configure_before_realize(self, window: object) -> None:
        """Assign the layer-shell role before the GTK window is mapped."""
        self._window = window
        _call_if_available(window, "set_decorated", False)
        _call_if_available(window, "set_app_paintable", True)
        _call_if_available(window, "set_accept_focus", False)
        _call_if_available(window, "set_focus_on_map", False)

        layer_shell = self._layer_shell
        layer_shell.init_for_window(window)
        _call_if_available(layer_shell, "set_namespace", window, "docking")
        _call_if_available(
            layer_shell,
            "set_layer",
            window,
            _enum_member(layer_shell, "Layer", "TOP"),
        )
        _call_if_available(
            layer_shell,
            "set_keyboard_mode",
            window,
            _enum_member(layer_shell, "KeyboardMode", "NONE"),
        )
        self._set_anchors(Position.BOTTOM)

    def on_realize(self, window: object) -> None:
        """Remember the realized GTK window and signal layer-surface readiness."""
        self._window = window
        if self._on_layer_surface_ready is not None:
            layer_surface = get_layer_surface(self._layer_shell, window)
            if layer_surface is not None:
                self._on_layer_surface_ready(layer_surface)

    def set_workspace_scope(self, *, current_workspace_only: bool) -> None:
        """Layer-shell surfaces are compositor-managed shell components."""

    def position_or_anchor(self, request: PlacementRequest) -> None:
        """Configure layer-shell anchors and GTK size from placement facts."""
        window = self._window
        if window is None:
            return

        position = request.position if isinstance(request.position, Position) else None
        if position is None:
            return

        self._set_monitor(request.monitor)
        self._set_anchors(position)
        self._set_margins(position=position, gap=0)
        _call_if_available(
            self._layer_shell,
            "set_size",
            window,
            request.size.width,
            request.size.height,
        )
        _call_if_available(
            window,
            "set_size_request",
            request.size.width,
            request.size.height,
        )
        _call_if_available(window, "resize", request.size.width, request.size.height)

    def set_reservation(self, request: ReservationRequest) -> None:
        """Reserve edge space using layer-shell exclusive zones."""
        self._exclusive_zone = max(0, int(request.thickness))
        self._apply_exclusive_zone()

    def clear_reservation(self) -> None:
        """Clear any layer-shell exclusive zone."""
        self._exclusive_zone = 0
        self._apply_exclusive_zone()

    def update_pointer_barrier(
        self,
        *,
        monitor: MonitorSnapshot | None,
        position: object,
        enabled: bool,
        pressure_callback: Callable[[], None] | None = None,
        pressure_threshold: int = 1,
    ) -> None:
        """Pointer barriers have no generic native Wayland equivalent."""

    def update_input_region(self, rect: Rect) -> None:
        """Apply the GTK/GDK input region for click-through transparent space."""
        window = self._window
        if window is None:
            return
        region = cairo.Region(
            cairo.RectangleInt(rect.x, rect.y, rect.width, rect.height)
        )
        input_shape = getattr(window, "input_shape_combine_region", None)
        if callable(input_shape):
            input_shape(region)
            return
        gdk_window = _call_if_available(window, "get_window")
        _call_if_available(gdk_window, "input_shape_combine_region", region, 0, 0)

    def set_blur_region(self, rect: Rect | None) -> None:
        """Blur hints are not part of the generic layer-shell path."""

    def _set_monitor(self, monitor: MonitorSnapshot) -> None:
        window = self._window
        if window is None:
            return
        display = _call_if_available(window, "get_display")
        if display is None:
            return
        gdk_monitor = _call_if_available(display, "get_monitor", monitor.index)
        if gdk_monitor is None:
            return
        _call_if_available(self._layer_shell, "set_monitor", window, gdk_monitor)

    def _set_anchors(self, position: Position) -> None:
        window = self._window
        if window is None:
            return
        edges = _edges(self._layer_shell)
        anchors = {
            "TOP": position in (Position.TOP, Position.LEFT, Position.RIGHT),
            "BOTTOM": position in (Position.BOTTOM, Position.LEFT, Position.RIGHT),
            "LEFT": position in (Position.LEFT, Position.TOP, Position.BOTTOM),
            "RIGHT": position in (Position.RIGHT, Position.TOP, Position.BOTTOM),
        }
        for edge_name, enabled in anchors.items():
            _call_if_available(
                self._layer_shell,
                "set_anchor",
                window,
                getattr(edges, edge_name),
                enabled,
            )

    def _set_margins(self, *, position: Position, gap: int) -> None:
        window = self._window
        if window is None:
            return
        edges = _edges(self._layer_shell)
        margins = {
            "TOP": gap if position is Position.TOP else 0,
            "BOTTOM": gap if position is Position.BOTTOM else 0,
            "LEFT": gap if position is Position.LEFT else 0,
            "RIGHT": gap if position is Position.RIGHT else 0,
        }
        for edge_name, margin in margins.items():
            _call_if_available(
                self._layer_shell,
                "set_margin",
                window,
                getattr(edges, edge_name),
                margin,
            )

    def _apply_exclusive_zone(self) -> None:
        window = self._window
        if window is None:
            return
        _call_if_available(
            self._layer_shell,
            "set_exclusive_zone",
            window,
            self._exclusive_zone,
        )


def _edges(layer_shell: object) -> object:
    edge = getattr(layer_shell, "Edge", None)
    if edge is not None:
        return edge
    return SimpleNamespace(TOP=1, BOTTOM=2, LEFT=4, RIGHT=8)


def _enum_member(
    layer_shell: object, enum_name: str, member_name: str
) -> object | None:
    enum_type = getattr(layer_shell, enum_name, None)
    if enum_type is None:
        return None
    return getattr(enum_type, member_name, None)


def _call_if_available(
    target: object | None, method_name: str, *args: object
) -> object | None:
    if target is None:
        return None
    method = getattr(target, method_name, None)
    if callable(method):
        return method(*args)
    return None


def get_layer_surface(layer_shell: object, window: object) -> object | None:
    """Return the zwlr_layer_surface_v1 for a layer-shell GTK window.

    Returns None before layer-shell initialization or when the compositor
    does not support the protocol.
    """
    getter = getattr(layer_shell, "get_zwlr_layer_surface_v1", None)
    if not callable(getter):
        return None
    try:
        return getter(window)
    except Exception:
        return None
