"""Phoc reservations must follow TOP panels without lowering the visible dock."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.core.position import Position
from docking.platform.backends.base import (
    MonitorSnapshot,
    PlacementRequest,
    Rect,
    ReservationRequest,
    Size,
)
from docking.platform.backends.wayland.phoc_surface import PhocLayerShellSurfaceService
from docking.platform.backends.wayland.runtime import WaylandProtocolRuntime


@pytest.mark.parametrize("position", list(Position))
def test_visible_dock_stays_top_and_reservation_follows_foreign_top_panels(position):
    layer = SimpleNamespace(
        init_for_window=MagicMock(),
        set_namespace=MagicMock(),
        set_layer=MagicMock(),
        set_keyboard_mode=MagicMock(),
        set_anchor=MagicMock(),
        set_margin=MagicMock(),
        set_monitor=MagicMock(),
        set_exclusive_zone=MagicMock(),
        Layer=SimpleNamespace(TOP=2, BOTTOM=1),
        KeyboardMode=SimpleNamespace(NONE=0),
        Edge=SimpleNamespace(TOP=1, BOTTOM=2, LEFT=3, RIGHT=4),
    )
    main, helper = MagicMock(), MagicMock()
    factory = MagicMock(return_value=helper)
    monitor = MonitorSnapshot(
        index=0, geometry=Rect(0, 0, 1280, 720), workarea=Rect(0, 0, 1280, 720)
    )
    service = PhocLayerShellSurfaceService(
        layer_shell=layer, reservation_window_factory=factory
    )
    service.configure_before_realize(main)
    service.position_or_anchor(
        PlacementRequest(
            monitor=monitor,
            position=position,
            x=0,
            y=0,
            size=Size(1280, 163),
        )
    )
    factory.assert_not_called()
    service.set_reservation(
        ReservationRequest(monitor=monitor, position=position, thickness=53)
    )
    factory.assert_called_once()
    layer.set_layer.assert_any_call(main, 2)
    layer.set_layer.assert_any_call(helper, 1)
    assert all(
        call.args[1] == 0
        for call in layer.set_exclusive_zone.call_args_list
        if call.args[0] is main
    )
    layer.set_exclusive_zone.assert_any_call(helper, 53)
    edge = getattr(layer.Edge, position.value.upper())
    layer.set_margin.assert_any_call(main, edge, -53)
    # The helper is transparent and non-interactive, not a second visible dock.
    assert helper.input_shape_combine_region.call_args.args[0].is_empty()
    helper.set_accept_focus.assert_called_once_with(False)
    service.clear_reservation()
    helper.hide.assert_called_once()
    layer.set_exclusive_zone.assert_any_call(helper, 0)
    layer.set_margin.assert_any_call(main, edge, 0)
    service.set_reservation(
        ReservationRequest(monitor=monitor, position=position, thickness=93)
    )
    factory.assert_called_once()  # reuse the owned helper after clearing
    layer.set_margin.assert_any_call(main, edge, -93)
    service.stop()
    helper.destroy.assert_called_once()


@pytest.mark.parametrize("version", [3, 7])
def test_phoc_detection_does_not_claim_the_shell_protocol(monkeypatch, version):
    monkeypatch.delenv("DOCKING_PHOC_PRIVATE_PREVIEWS", raising=False)
    runtime = WaylandProtocolRuntime()
    registry = MagicMock()
    runtime._on_global(registry, 7, "phosh_private", version)
    assert runtime.is_phoc
    registry.bind.assert_not_called()
    runtime._on_global_remove(registry, 7)
    assert not runtime.is_phoc
