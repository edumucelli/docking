"""AppBar dimensions stay in monitor space across DPI and negative origins."""

from unittest.mock import Mock

import pytest

from docking.core.position import Position
from docking.platform.backends.base import Rect
from docking.platform.backends.windows import appbar
from docking.platform.backends.windows.appbar import edge_strip
from docking.platform.backends.windows.geometry import ScreenMapping


@pytest.mark.parametrize(
    "identity,registered", [((101, 202), True), ((303, 404), False)]
)
def test_taskbar_broadcast_preserves_registration_until_the_actual_shell_changes(
    monkeypatch, identity, registered
):
    bar = appbar.WindowsAppBar.__new__(appbar.WindowsAppBar)
    bar._closed = False
    bar._registered = True
    bar._registered_shell = (101, 202)
    bar._reserved = Rect(0, 681, 1280, 71)
    bar._message = 0xC001
    bar._taskbar_created = 0xC002
    bar._user = object()
    bar._on_change = Mock()
    bar._default = Mock(return_value=0)
    monkeypatch.setattr(appbar, "explorer_tray", lambda *args, **kwargs: identity)
    assert bar._window_proc(10, 0xC002, 0, 0) == 0
    assert bar._registered is registered and bar._reserved is None
    bar._on_change.assert_called_once_with()


def test_left_hand_scaled_monitor_reserves_the_resting_shelf_in_device_pixels():
    mapping = ScreenMapping(
        logical=Rect(0, 0, 1280, 720),
        physical=Rect(-2560, 0, 2560, 1440),
        workarea=Rect(-2560, 0, 2560, 1344),
        scale=2,
    )
    strip = edge_strip(mapping.workarea, Position.BOTTOM, 60 * mapping.scale)
    assert strip == Rect(-2560, 1224, 2560, 120)
    logical = mapping.to_logical(strip)
    assert logical == Rect(0, 612, 1280, 60)
    assert mapping.to_physical(logical) == strip


def test_foreign_window_geometry_rounds_outward_relative_to_the_dock_monitor():
    mapping = ScreenMapping(
        Rect(1280, 0, 1280, 720), Rect(0, 0, 2560, 1440), Rect(0, 0, 2560, 1344), 2
    )
    assert mapping.to_logical(Rect(-1, -1, 3, 3)) == Rect(1279, -1, 2, 2)


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        (Position.BOTTOM, Rect(-1920, 964, 1920, 60)),
        (Position.TOP, Rect(-1920, 24, 1920, 60)),
        (Position.LEFT, Rect(-1920, 24, 60, 1000)),
        (Position.RIGHT, Rect(-60, 24, 60, 1000)),
    ],
)
def test_all_edges_use_the_available_desktop_beyond_external_panels(position, expected):
    assert edge_strip(Rect(-1920, 24, 1920, 1000), position, 60) == expected
