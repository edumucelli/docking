"""Native overlay positions retain odd pixels without replacing GTK input."""

import ctypes
from collections import deque
from ctypes import wintypes
from unittest.mock import Mock

from docking.applets.screenshot.windows_selection import WindowsRegionCoordinates
from docking.platform.backends.base import Rect
from docking.platform.backends.windows.geometry import ScreenMapping


def coordinates():
    value = WindowsRegionCoordinates.__new__(WindowsRegionCoordinates)
    value._hwnd = 100
    value._positions = {key: deque(maxlen=32) for key in ("press", "motion", "release")}
    value._mappings = [
        ScreenMapping(
            Rect(-640, 0, 640, 400),
            Rect(-1280, -100, 1280, 800),
            Rect(-1280, -100, 1280, 800),
            2,
        )
    ]

    def to_screen(_hwnd, pointer):
        point = ctypes.cast(pointer, ctypes.POINTER(wintypes.POINT)).contents
        point.x -= 1280
        point.y -= 100
        return 1

    value._to_screen = to_screen
    value._next = Mock(return_value=77)
    value._remove = Mock(return_value=1)
    value._callback = object()
    return value


def test_observer_keeps_queued_button_positions_and_delegates_every_message():
    value = coordinates()
    for x, y in ((51, 61), (101, 81)):
        assert value._message(100, 0x201, 0, (y << 16) | x, 0, 0) == 77
    assert value.point("press") == (-614.5, 30.5)
    assert value.point("press") == (-589.5, 40.5)
    assert value.point("press") is None
    value._next.assert_any_call(100, 0x201, 0, (61 << 16) | 51)


def test_destroy_removes_only_the_owned_observer_and_discards_stale_positions():
    value = coordinates()
    value._message(100, 0x202, 0, (61 << 16) | 51, 0, 0)
    assert value._message(100, 0x82, 0, 0, 0, 0) == 77
    value.close()
    value._remove.assert_called_once_with(100, value._callback, id(value))
    assert value.point("release") is None
