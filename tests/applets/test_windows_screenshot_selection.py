"""Native overlay positions retain odd pixels without replacing GTK input."""

import ctypes
from collections import deque
from ctypes import wintypes
from unittest.mock import Mock

from docking.applets.screenshot.implementations.windows.selection import (
    WindowsRegionCoordinates,
)
from docking.platform.backends.base import Rect
from docking.platform.desktop.implementations.windows.geometry import ScreenMapping


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


def test_selection_retains_the_other_monitor_when_one_disappears(monkeypatch):
    from docking.applets.screenshot.implementations.windows import selection

    mapping = ScreenMapping(
        Rect(0, 0, 800, 600), Rect(0, 0, 800, 600), Rect(0, 0, 800, 560), 1
    )
    geometry = Mock()
    geometry.mapping.return_value = mapping
    monkeypatch.setattr(selection, "WindowsMonitorGeometry", lambda: geometry)
    library = Mock()
    library.SetWindowSubclass.return_value = 1
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *_args, **_kwargs: library, raising=False
    )
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    monkeypatch.setattr(selection, "window_handle", lambda _window: 100)
    window, monitor = Mock(), Mock()
    window.get_display().get_n_monitors.return_value = 2
    window.get_display().get_monitor.side_effect = [None, monitor]
    monitor.get_geometry.return_value = Rect(0, 0, 800, 600)
    monitor.get_scale_factor.return_value = 1

    coordinates = WindowsRegionCoordinates(window)
    try:
        assert coordinates._mappings == [mapping]
        assert geometry.mapping.call_args.args[0].index == 1
        library.SetWindowSubclass.assert_called_once()
    finally:
        coordinates.close()
    library.RemoveWindowSubclass.assert_called_once()


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
