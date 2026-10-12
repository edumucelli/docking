"""Keep Phoc's exclusive-panel ordering separate from the visible TOP dock."""

from __future__ import annotations

from collections.abc import Callable

import cairo

from docking.core.position import Position, is_horizontal
from docking.platform.backends.base import PlacementRequest, ReservationRequest
from docking.platform.backends.wayland.services import (
    WaylandLayerShellSurfaceService,
    _call_if_available,
    _edges,
    _enum_member,
)


def _reservation_window() -> object:
    from gi.repository import Gtk

    return Gtk.Window()


class PhocLayerShellSurfaceService(WaylandLayerShellSurfaceService):
    """Reserve below TOP panels, and keep the visible dock above applications.

    Phoc arranges exclusive surfaces in reverse creation order within a layer.
    A newly started exclusive TOP dock can therefore displace an existing TOP
    panel. A transparent BOTTOM reservation follows those panels independently
    of startup order. The non-exclusive TOP dock cancels only its own reservation
    with an equal negative margin; foreign panels still displace it normally.
    """

    def __init__(
        self,
        *,
        layer_shell: object,
        reservation_window_factory: Callable[[], object] = _reservation_window,
    ) -> None:
        super().__init__(layer_shell=layer_shell)
        self._reservation_factory = reservation_window_factory
        self._reservation_window: object | None = None
        self._position: Position | None = None

    def position_or_anchor(self, request: PlacementRequest) -> None:
        self._position = (
            request.position if isinstance(request.position, Position) else None
        )
        super().position_or_anchor(request)
        self._apply_exclusive_zone()

    def set_reservation(self, request: ReservationRequest) -> None:
        self._position = (
            request.position if isinstance(request.position, Position) else None
        )
        self._monitor = request.monitor
        super().set_reservation(request)

    def _set_margins(self, *, position: Position, gap: int) -> None:
        super()._set_margins(position=position, gap=-self._exclusive_zone)

    def _apply_exclusive_zone(self) -> None:
        window, position = self._window, self._position
        if window is None or position is None:
            return
        _call_if_available(self._layer_shell, "set_exclusive_zone", window, 0)
        self._set_margins(position=position, gap=0)
        helper = self._reservation_window
        if self._exclusive_zone == 0:
            if helper is not None:
                _call_if_available(self._layer_shell, "set_exclusive_zone", helper, 0)
                _call_if_available(helper, "hide")
            return
        if helper is None:
            helper = self._reservation_factory()
            self._reservation_window = helper
            _call_if_available(helper, "set_decorated", False)
            _call_if_available(helper, "set_app_paintable", True)
            _call_if_available(helper, "set_accept_focus", False)
            _call_if_available(helper, "set_focus_on_map", False)
            screen = _call_if_available(helper, "get_screen")
            visual = _call_if_available(screen, "get_rgba_visual")
            if visual is not None:
                _call_if_available(helper, "set_visual", visual)
            _call_if_available(helper, "connect", "draw", self._draw_reservation)
            self._layer_shell.init_for_window(helper)
            _call_if_available(
                self._layer_shell, "set_namespace", helper, "docking-reservation"
            )
            _call_if_available(
                self._layer_shell,
                "set_layer",
                helper,
                _enum_member(self._layer_shell, "Layer", "BOTTOM"),
            )
            _call_if_available(
                self._layer_shell,
                "set_keyboard_mode",
                helper,
                _enum_member(self._layer_shell, "KeyboardMode", "NONE"),
            )
        if self._monitor is not None:
            self._set_monitor_for_window(helper, self._monitor)
        edges = _edges(self._layer_shell)
        horizontal = is_horizontal(position)
        for name, anchored in {
            "TOP": position in (Position.TOP, Position.LEFT, Position.RIGHT),
            "BOTTOM": position in (Position.BOTTOM, Position.LEFT, Position.RIGHT),
            "LEFT": position in (Position.LEFT, Position.TOP, Position.BOTTOM),
            "RIGHT": position in (Position.RIGHT, Position.TOP, Position.BOTTOM),
        }.items():
            _call_if_available(
                self._layer_shell, "set_anchor", helper, getattr(edges, name), anchored
            )
        _call_if_available(
            helper, "set_size_request", -1 if horizontal else 1, 1 if horizontal else -1
        )
        _call_if_available(
            self._layer_shell, "set_exclusive_zone", helper, self._exclusive_zone
        )
        _call_if_available(helper, "input_shape_combine_region", cairo.Region([]))
        _call_if_available(helper, "show_all")

    @staticmethod
    def _draw_reservation(_widget, cr) -> bool:
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        return True

    def stop(self) -> None:
        if self._reservation_window is not None:
            _call_if_available(self._reservation_window, "destroy")
        self._reservation_window = None
        self._position = None
        self._exclusive_zone = 0
        super().stop()
