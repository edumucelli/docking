"""Quiet stacking survives realization and reverses when another mode is selected."""

from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from docking.core.position import Position
from docking.platform.backends.base import (
    MonitorSnapshot,
    PlacementRequest,
    Rect,
    ReservationRequest,
    Size,
)
from docking.platform.backends.cinnamon.shell import CinnamonShellSurfaceService
from docking.platform.backends.reduced.services import ReducedSurfaceService
from docking.platform.backends.wayland.phoc_surface import PhocLayerShellSurfaceService
from docking.platform.backends.wayland.services import WaylandLayerShellSurfaceService
from docking.platform.backends.x11.services.surface import X11SurfaceService


def _request(keep_above):
    return PlacementRequest(
        monitor=MonitorSnapshot(0, Rect(0, 0, 1280, 800)),
        position=Position.BOTTOM,
        x=0,
        y=700,
        size=Size(1280, 100),
        keep_above=keep_above,
    )


@pytest.mark.parametrize(
    "surface_type",
    [
        X11SurfaceService,
        ReducedSurfaceService,
        pytest.param(
            lambda: CinnamonShellSurfaceService(client=MagicMock()), id="cinnamon"
        ),
    ],
)
def test_gtk_surfaces_clear_opposite_hint_before_applying_stacking(
    surface_type, monkeypatch
):
    monkeypatch.setattr(
        "docking.platform.backends.cinnamon.shell.GLib.timeout_add", lambda *_args: 77
    )
    monkeypatch.setattr(
        "docking.platform.backends.cinnamon.shell.GLib.source_remove", MagicMock()
    )
    service = surface_type()
    window = MagicMock()
    service.configure_before_realize(window, keep_above=False)
    service.on_realize(window)
    assert all(args == call(False) for args in window.set_keep_above.call_args_list)
    assert all(args == call(True) for args in window.set_keep_below.call_args_list)
    for keep_above in (False, True, False):
        window.reset_mock()
        service.position_or_anchor(_request(keep_above))
        expected = (
            [call.set_keep_below(False), call.set_keep_above(True)]
            if keep_above
            else [call.set_keep_above(False), call.set_keep_below(True)]
        )
        assert window.mock_calls[:2] == expected
        window.present.assert_not_called()
    service.stop()


@pytest.mark.parametrize(
    "surface_type", [WaylandLayerShellSurfaceService, PhocLayerShellSurfaceService]
)
def test_layer_surfaces_restore_layer_and_clear_reservation_helpers(surface_type):
    layer = MagicMock()
    layer.Layer = SimpleNamespace(TOP="top", BOTTOM="bottom")
    main, helper = MagicMock(), MagicMock()
    if surface_type is PhocLayerShellSurfaceService:
        service = surface_type(
            layer_shell=layer, reservation_window_factory=lambda: helper
        )
    else:
        service = surface_type(layer_shell=layer)
    service.configure_before_realize(main, keep_above=False)
    layer.set_layer.assert_called_once_with(main, "bottom")
    service.on_realize(main)
    for keep_above in (False, True, False):
        request = _request(keep_above)
        service.position_or_anchor(request)
        layer.set_layer.assert_called_with(main, "top" if keep_above else "bottom")
        service.set_reservation(
            ReservationRequest(request.monitor, request.position, thickness=48)
        )
        service.clear_reservation()
        layer.set_exclusive_zone.assert_any_call(main, 0)
        if surface_type is PhocLayerShellSurfaceService:
            helper.hide.assert_called()
    service.stop()
    if surface_type is PhocLayerShellSurfaceService:
        helper.destroy.assert_called_once_with()


@pytest.mark.parametrize("native_commit", [False, True])
def test_layer_change_commits_even_when_the_bottom_surface_is_occluded(native_commit):
    layer = MagicMock()
    layer.Layer = SimpleNamespace(TOP="top", BOTTOM="bottom")
    if not native_commit:
        layer.try_force_commit = None
    service = WaylandLayerShellSurfaceService(layer_shell=layer)
    window = MagicMock()
    service.configure_before_realize(window, keep_above=False)
    for keep_above in (False, True, True, False):
        service.position_or_anchor(_request(keep_above))
    if native_commit:
        assert layer.try_force_commit.call_count == 2
        window.hide.assert_not_called()
    else:
        assert window.hide.call_count == window.show.call_count == 2
