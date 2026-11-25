"""Keep Phoc's exclusive-panel ordering separate from the visible TOP dock."""

from __future__ import annotations

from collections.abc import Callable

import cairo

from docking.core.position import Position, is_horizontal
from docking.platform.backends.base import PlacementRequest, ReservationRequest
from docking.platform.backends.wayland.services import (
    WaylandLayerShellSurfaceService,
    call_if_available,
    enum_member,
    layer_shell_edges,
)


def _reservation_window() -> object:
    from gi.repository import Gtk

    return Gtk.Window()


class PhocLayerShellSurfaceService(WaylandLayerShellSurfaceService):
    """Reserve below TOP panels, and keep the visible dock above applications.

    Phoc arranges exclusive surfaces oldest first within a layer. An exclusive
    TOP dock can therefore displace a TOP panel that starts or restarts later.
    A transparent BOTTOM reservation follows those panels independently
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
        # A TOP-edge example. F is the height of the foreign TOP panel and
        # D is this dock's reservation. Layer-shell places the BOTTOM-layer
        # helper after the foreign TOP panel, regardless of creation order.
        #
        #   y = 0       +------------------------------------+ output edge
        #               | foreign TOP panel                 | F
        #   y = F       +------------------------------------+ visible dock starts
        #               | dock space reserved by the helper | D
        #   y = F + D   +------------------------------------+ application workarea
        #               ^
        #               default start of the non-exclusive dock
        #
        # Move the visible dock outward by D with a negative edge margin.
        # It cancels the helper's inset and leaves F intact. The helper draws
        # no pixels and takes no pointer input, so the visible dock controls
        # both appearance and interaction in the reserved band.
        call_if_available(self._layer_shell, "set_exclusive_zone", window, 0)
        self._set_margins(position=position, gap=0)
        helper = self._reservation_window
        if self._exclusive_zone == 0:
            if helper is not None:
                call_if_available(self._layer_shell, "set_exclusive_zone", helper, 0)
                call_if_available(helper, "hide")
            return
        if helper is None:
            helper = self._reservation_factory()
            self._reservation_window = helper
            call_if_available(helper, "set_decorated", False)
            call_if_available(helper, "set_app_paintable", True)
            call_if_available(helper, "set_accept_focus", False)
            call_if_available(helper, "set_focus_on_map", False)
            screen = call_if_available(helper, "get_screen")
            visual = call_if_available(screen, "get_rgba_visual")
            if visual is not None:
                call_if_available(helper, "set_visual", visual)
            call_if_available(helper, "connect", "draw", self._draw_reservation)
            self._layer_shell.init_for_window(helper)
            call_if_available(
                self._layer_shell, "set_namespace", helper, "docking-reservation"
            )
            call_if_available(
                self._layer_shell,
                "set_layer",
                helper,
                enum_member(self._layer_shell, "Layer", "BOTTOM"),
            )
            call_if_available(
                self._layer_shell,
                "set_keyboard_mode",
                helper,
                enum_member(self._layer_shell, "KeyboardMode", "NONE"),
            )
        if self._monitor is not None:
            self._set_monitor_for_window(helper, self._monitor)
        edges = layer_shell_edges(self._layer_shell)
        horizontal = is_horizontal(position)
        for name, anchored in {
            "TOP": position in (Position.TOP, Position.LEFT, Position.RIGHT),
            "BOTTOM": position in (Position.BOTTOM, Position.LEFT, Position.RIGHT),
            "LEFT": position in (Position.LEFT, Position.TOP, Position.BOTTOM),
            "RIGHT": position in (Position.RIGHT, Position.TOP, Position.BOTTOM),
        }.items():
            call_if_available(
                self._layer_shell, "set_anchor", helper, getattr(edges, name), anchored
            )
        call_if_available(
            helper, "set_size_request", -1 if horizontal else 1, 1 if horizontal else -1
        )
        call_if_available(
            self._layer_shell, "set_exclusive_zone", helper, self._exclusive_zone
        )
        call_if_available(helper, "input_shape_combine_region", cairo.Region([]))
        call_if_available(helper, "show_all")

    @staticmethod
    def _draw_reservation(_widget, cr) -> bool:
        cr.set_operator(cairo.OPERATOR_CLEAR)
        cr.paint()
        return True

    def stop(self) -> None:
        if self._reservation_window is not None:
            call_if_available(self._reservation_window, "destroy")
        self._reservation_window = None
        self._position = None
        self._exclusive_zone = 0
        super().stop()
