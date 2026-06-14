"""Tests for the X11 SurfaceService implementation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.core.position import Position
from docking.platform.backends.base import (
    MonitorSnapshot,
    PlacementRequest,
    Rect,
    ReservationRequest,
    Size,
)
from docking.platform.backends.x11.services import surface as surface_mod
from docking.platform.backends.x11.services.surface import X11SurfaceService


class FakeX11Window:
    def get_scale_factor(self) -> int:
        return 2


def _monitor_snapshot(scale: int = 1) -> MonitorSnapshot:
    return MonitorSnapshot(
        index=0,
        geometry=Rect(x=10, y=20, width=300, height=200),
        workarea=Rect(x=10, y=20, width=300, height=180),
        scale=scale,
        primary=True,
    )


def test_configure_before_realize_applies_x11_dock_hints():
    window = MagicMock()
    service = X11SurfaceService()

    service.configure_before_realize(window)

    window.set_skip_taskbar_hint.assert_called_once_with(True)
    window.set_skip_pager_hint.assert_called_once_with(True)
    window.stick.assert_called_once_with()
    window.set_keep_above.assert_called_once_with(True)
    window.set_type_hint.assert_called_once_with(surface_mod.Gdk.WindowTypeHint.DOCK)


def test_x11_surface_service_satisfies_abstract_contract():
    assert X11SurfaceService.__abstractmethods__ == frozenset()


def test_on_realize_initializes_pointer_barrier_for_x11_display(monkeypatch):
    class FakeX11Display:
        pass

    monkeypatch.setattr(surface_mod.GdkX11, "X11Display", FakeX11Display, raising=False)
    display = FakeX11Display()
    window = MagicMock()
    window.get_display.return_value = display
    barrier = MagicMock()
    service = X11SurfaceService(barrier=barrier)

    service.on_realize(window)

    barrier.initialize.assert_called_once_with(gdk_display=display)


def test_set_workspace_scope_toggles_x11_stickiness():
    window = MagicMock()
    service = X11SurfaceService()
    service.configure_before_realize(window)

    service.set_workspace_scope(current_workspace_only=True)
    service.set_workspace_scope(current_workspace_only=False)

    window.unstick.assert_called_once_with()
    assert window.stick.call_count == 2


def test_position_or_anchor_moves_and_resizes_window():
    window = MagicMock()
    service = X11SurfaceService()
    service.configure_before_realize(window)

    service.position_or_anchor(
        PlacementRequest(
            monitor=_monitor_snapshot(),
            position=Position.BOTTOM,
            x=11,
            y=22,
            size=Size(width=333, height=44),
        )
    )

    window.set_size_request.assert_called_once_with(333, 44)
    window.resize.assert_called_once_with(333, 44)
    window.move.assert_called_once_with(11, 22)


def test_set_reservation_writes_struts_for_x11_window(monkeypatch):
    monkeypatch.setattr(surface_mod.GdkX11, "X11Window", FakeX11Window, raising=False)
    set_struts = MagicMock()
    monkeypatch.setattr(surface_mod, "set_dock_struts", set_struts)
    x11_window = FakeX11Window()
    screen = MagicMock()
    window = MagicMock()
    window.get_window.return_value = x11_window
    window.get_screen.return_value = screen
    service = X11SurfaceService()
    service.configure_before_realize(window)

    service.set_reservation(
        ReservationRequest(
            monitor=_monitor_snapshot(),
            position=Position.BOTTOM,
            thickness=56,
        )
    )

    kwargs = set_struts.call_args.kwargs
    assert kwargs["gdk_window"] is x11_window
    assert kwargs["dock_height"] == 56
    assert kwargs["monitor_geom"].x == 10
    assert kwargs["monitor_geom"].y == 20
    assert kwargs["monitor_geom"].width == 300
    assert kwargs["monitor_geom"].height == 200
    assert kwargs["screen"] is screen
    assert kwargs["position"] == Position.BOTTOM


def test_clear_reservation_clears_x11_struts(monkeypatch):
    monkeypatch.setattr(surface_mod.GdkX11, "X11Window", FakeX11Window, raising=False)
    clear = MagicMock()
    monkeypatch.setattr(surface_mod, "clear_struts", clear)
    x11_window = FakeX11Window()
    window = MagicMock()
    window.get_window.return_value = x11_window
    service = X11SurfaceService()
    service.configure_before_realize(window)

    service.clear_reservation()

    clear.assert_called_once_with(gdk_window=x11_window)


def test_update_pointer_barrier_updates_and_wires_pressure_handler():
    callback = MagicMock()
    barrier = MagicMock(supported=True)
    service = X11SurfaceService(barrier=barrier)

    service.update_pointer_barrier(
        monitor=_monitor_snapshot(scale=2),
        position=Position.RIGHT,
        enabled=True,
        pressure_callback=callback,
        pressure_threshold=25,
    )

    barrier.update.assert_called_once_with(
        position=Position.RIGHT,
        monitor_x=10,
        monitor_y=20,
        monitor_w=300,
        monitor_h=200,
        scale=2,
    )
    barrier.set_pressure_handler.assert_called_once_with(
        callback=callback,
        threshold=25,
    )


def test_update_pointer_barrier_clears_when_disabled_or_missing_monitor():
    barrier = MagicMock(supported=True)
    service = X11SurfaceService(barrier=barrier)

    service.update_pointer_barrier(
        monitor=None,
        position=Position.BOTTOM,
        enabled=False,
    )

    barrier.destroy.assert_called_once_with()


def test_update_input_region_applies_shape_to_window():
    x11_window = MagicMock()
    window = MagicMock()
    window.get_window.return_value = x11_window
    service = X11SurfaceService()
    service.configure_before_realize(window)

    service.update_input_region(Rect(x=1, y=2, width=30, height=40))

    region = x11_window.input_shape_combine_region.call_args.args[0]
    extents = region.get_extents()
    assert (extents.x, extents.y, extents.width, extents.height) == (1, 2, 30, 40)


def test_set_blur_region_uses_window_theme_and_config(monkeypatch):
    monkeypatch.setattr(surface_mod.GdkX11, "X11Window", FakeX11Window, raising=False)
    set_blur = MagicMock()
    monkeypatch.setattr(surface_mod, "set_blur_region", set_blur)
    x11_window = FakeX11Window()
    window = MagicMock()
    window.get_window.return_value = x11_window
    window.theme = SimpleNamespace(roundness=4.0, round_bottom=True)
    window.config = SimpleNamespace(pos=Position.BOTTOM)
    service = X11SurfaceService()
    service.configure_before_realize(window)

    service.set_blur_region(Rect(x=1, y=2, width=30, height=40))

    set_blur.assert_called_once_with(
        gdk_window=x11_window,
        blur_region=[2, 4, 60, 80, 8, 8, 8, 8],
    )


def test_set_blur_region_none_clears_hint(monkeypatch):
    monkeypatch.setattr(surface_mod.GdkX11, "X11Window", FakeX11Window, raising=False)
    clear = MagicMock()
    monkeypatch.setattr(surface_mod, "clear_blur_region", clear)
    x11_window = FakeX11Window()
    window = MagicMock()
    window.get_window.return_value = x11_window
    service = X11SurfaceService()
    service.configure_before_realize(window)

    service.set_blur_region(None)

    clear.assert_called_once_with(gdk_window=x11_window)
