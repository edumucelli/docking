"""Platform coordinate projection keeps GTK geometry out of tray transports."""

from types import SimpleNamespace
from unittest.mock import Mock

from docking.platform.backends.base import MonitorSnapshot, Rect
from docking.platform.backends.gtk_surface import logical_screen_rectangle
from docking.platform.backends.reduced.services import ReducedSurfaceService
from docking.platform.backends.windows.appbar import ScreenMapping
from docking.platform.backends.windows.surface import WindowsSurfaceService


def window(*, mapped=True, origin=(1, 10, 50)):
    native = Mock()
    native.get_origin.return_value = origin
    result = Mock()
    result.get_window.return_value = native
    result.get_mapped.return_value = mapped
    return result


def test_gtk_coordinates_remain_relative_to_the_native_client_origin():
    assert logical_screen_rectangle(
        window(origin=(1, -1200, 80)), Rect(4, 5, 24, 24)
    ) == Rect(-1196, 85, 24, 24)


def test_unmapped_or_unknown_origins_are_not_published():
    assert logical_screen_rectangle(window(mapped=False), Rect(4, 5, 24, 24)) is None
    assert (
        logical_screen_rectangle(window(origin=(0, 0, 0)), Rect(4, 5, 24, 24)) is None
    )
    source = window()
    source.get_window.return_value = None
    assert logical_screen_rectangle(source, Rect(4, 5, 24, 24)) is None


def test_windows_projection_uses_popup_monitor_scale_and_negative_physical_origin():
    source = window()
    monitor = Mock()
    monitor.get_geometry.return_value = SimpleNamespace(
        x=0, y=0, width=1280, height=720
    )
    monitor.get_scale_factor.return_value = 2
    display = source.get_display.return_value
    display.get_monitor_at_window.return_value = monitor
    display.get_n_monitors.return_value = 1
    display.get_monitor.return_value = monitor
    surface = WindowsSurfaceService()
    surface._appbar = Mock()
    surface._appbar.mapping.return_value = ScreenMapping(
        Rect(0, 0, 1280, 720), Rect(-2560, 0, 2560, 1440), Rect(-2560, 0, 2560, 1344), 2
    )
    assert surface.screen_rectangle(source, Rect(4, 5, 24, 24)) == Rect(
        -2532, 110, 48, 48
    )
    snapshot, actual_display = surface._appbar.mapping.call_args.args
    assert snapshot.index == 0 and snapshot.scale == 2 and actual_display is display


def test_windows_unrealized_surface_has_no_guessed_screen_coordinates():
    assert (
        WindowsSurfaceService().screen_rectangle(window(), Rect(0, 0, 24, 24)) is None
    )


def test_monitor_workarea_defaults_to_the_toolkit_reservations():
    monitor = MonitorSnapshot(
        index=0, geometry=Rect(0, 0, 1280, 800), workarea=Rect(0, 0, 1280, 699)
    )
    assert ReducedSurfaceService().monitor_workarea(monitor) == monitor.workarea
    assert WindowsSurfaceService().monitor_workarea(monitor) == monitor.workarea


def test_windows_workarea_includes_own_live_reservation_with_scaled_negative_origin():
    monitor = MonitorSnapshot(
        index=0,
        geometry=Rect(-1280, 0, 1280, 720),
        workarea=Rect(-1280, 0, 1280, 720),
        scale=2,
    )
    surface = WindowsSurfaceService()
    surface._window = window()
    surface._appbar = Mock()
    surface._appbar.mapping.return_value = ScreenMapping(
        monitor.geometry,
        Rect(-2560, 0, 2560, 1440),
        Rect(-2560, 0, 2560, 1292),
        2,
    )
    assert surface.monitor_workarea(monitor) == Rect(-1280, 0, 1280, 646)
    surface._appbar.external.assert_not_called()


def test_windows_workarea_query_failure_allows_toolkit_fallback():
    monitor = MonitorSnapshot(index=0, geometry=Rect(0, 0, 1280, 800))
    surface = WindowsSurfaceService()
    surface._window = window()
    surface._appbar = Mock()
    surface._appbar.mapping.side_effect = OSError("Monitor removed")
    assert surface.monitor_workarea(monitor) is None
