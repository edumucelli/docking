"""Windows owns its surface lifecycle while reusing common GTK operations."""

from unittest.mock import Mock, call

from docking.core.position import Position
from docking.platform.backends.base import MonitorSnapshot, PlacementRequest, Rect, Size
from docking.platform.backends.reduced.services import ReducedSurfaceService
from docking.platform.backends.windows.surface import WindowsSurfaceService


def test_windows_surface_preserves_gtk_hints_and_placement_without_reduced_services(
    monkeypatch,
):
    forbidden = Mock(side_effect=AssertionError("Reduced surface must be independent"))
    monkeypatch.setattr(ReducedSurfaceService, "__init__", forbidden)
    service = WindowsSurfaceService()
    window = Mock()
    placement = PlacementRequest(
        monitor=MonitorSnapshot(index=0, geometry=Rect(0, 0, 1280, 800)),
        position=Position.BOTTOM,
        x=10,
        y=20,
        size=Size(300, 40),
    )
    service.start()
    service.position_or_anchor(placement)
    window.move.assert_not_called()
    service.configure_before_realize(window)
    # Native resources are tested separately; this check exercises the GTK
    # lifecycle on every host without loading Windows DLLs.
    service._appbar = appbar = Mock()
    service._menus = menus = Mock()
    service.on_realize(window)
    assert window.set_skip_taskbar_hint.call_args_list == [call(True), call(True)]
    assert window.set_skip_pager_hint.call_args_list == [call(True), call(True)]
    assert window.set_accept_focus.call_args_list == [call(False), call(False)]
    assert window.set_focus_on_map.call_args_list == [call(False), call(False)]
    assert window.stick.call_count == 2
    assert window.set_keep_above.call_args_list == [call(True), call(True)]
    assert window.set_type_hint.call_count == 2
    menus.bind_parent.assert_called_once_with(window.get_window.return_value)
    service.position_or_anchor(placement)
    window.set_size_request.assert_called_once_with(300, 40)
    window.resize.assert_called_once_with(300, 40)
    window.move.assert_called_once_with(10, 20)
    service.set_blur_region(None)
    service.stop()
    appbar.close.assert_called_once_with()
    menus.stop.assert_called_once_with()
    forbidden.assert_not_called()
