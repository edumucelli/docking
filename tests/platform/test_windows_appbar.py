"""AppBar dimensions stay in monitor space across DPI and negative origins."""

import ctypes
import os
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.core.position import Position
from docking.platform.backends.base import Rect
from docking.platform.backends.windows import appbar
from docking.platform.backends.windows.appbar import edge_strip
from docking.platform.backends.windows.geometry import ScreenMapping


@pytest.fixture
def initial_fullscreen():
    state = {
        "owner": os.getpid() + 1,
        "class": "Owned fullscreen fixture",
        "rect": (0, 0, 1280, 800),
    }

    def owner(_window, pointer):
        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD)).contents.value = state[
            "owner"
        ]
        return 123

    def name(_window, buffer, _length):
        buffer.value = state["class"]
        return len(buffer.value)

    def rectangle(_window, pointer):
        result = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
        result.left, result.top, result.right, result.bottom = state["rect"]
        return 1

    user = SimpleNamespace(
        GetForegroundWindow=Mock(return_value=100),
        GetWindowThreadProcessId=Mock(side_effect=owner),
        GetClassNameW=Mock(side_effect=name),
        IsWindowVisible=Mock(return_value=1),
        IsIconic=Mock(return_value=0),
        GetWindowRect=Mock(side_effect=rectangle),
    )
    bar = appbar.WindowsAppBar.__new__(appbar.WindowsAppBar)
    bar._closed = False
    bar._user = user
    bar._on_fullscreen = Mock()
    mapping = ScreenMapping(
        Rect(0, 0, 1280, 800), Rect(0, 0, 1280, 800), Rect(0, 0, 1280, 752), 1
    )
    return bar, user, state, mapping


@pytest.mark.parametrize(
    "bounds,fullscreen",
    [
        ((0, 0, 1280, 800), True),
        ((60, 60, 676, 479), False),
        ((1280, 0, 2560, 800), False),
        ((-100, 0, 1280, 800), True),
        ((0, 0, 1280, 799), False),
    ],
)
def test_initial_fullscreen_is_scoped_to_the_dock_monitor(
    initial_fullscreen, bounds, fullscreen
):
    bar, _user, state, mapping = initial_fullscreen
    state["rect"] = bounds
    assert bar.fullscreen_active(mapping) is fullscreen
    bar._on_fullscreen.assert_not_called()


@pytest.mark.parametrize("kind", ["own", "desktop", "taskbar", "hidden", "minimized"])
def test_initial_fullscreen_excludes_non_application_surfaces(initial_fullscreen, kind):
    bar, user, state, mapping = initial_fullscreen
    if kind == "own":
        state["owner"] = os.getpid()
    elif kind == "desktop":
        state["class"] = "Progman"
    elif kind == "taskbar":
        state["class"] = "Shell_TrayWnd"
    elif kind == "hidden":
        user.IsWindowVisible.return_value = 0
    else:
        user.IsIconic.return_value = 1
    assert bar.fullscreen_active(mapping) is False
    bar._on_fullscreen.assert_not_called()
    user.GetWindowRect.assert_not_called()


@pytest.mark.parametrize("failure", ["foreground", "owner", "class", "rectangle"])
def test_incomplete_initial_foreground_read_does_not_override_shell_state(
    initial_fullscreen, failure
):
    bar, user, _state, mapping = initial_fullscreen
    function = {
        "foreground": user.GetForegroundWindow,
        "owner": user.GetWindowThreadProcessId,
        "class": user.GetClassNameW,
        "rectangle": user.GetWindowRect,
    }[failure]
    function.side_effect = None
    function.return_value = 0
    assert bar.fullscreen_active(mapping) is None
    bar._on_fullscreen.assert_not_called()


def test_initial_fullscreen_read_uses_physical_negative_monitor_bounds(
    initial_fullscreen,
):
    bar, _user, state, _mapping = initial_fullscreen
    state["rect"] = (-2560, 0, 0, 1600)
    mapping = ScreenMapping(
        Rect(0, 0, 1280, 800),
        Rect(-2560, 0, 2560, 1600),
        Rect(-2560, 0, 2560, 1504),
        2,
    )
    assert bar.fullscreen_active(mapping) is True
    bar._on_fullscreen.assert_not_called()


def test_closed_appbar_cannot_read_or_change_initial_stacking(initial_fullscreen):
    bar, user, _state, mapping = initial_fullscreen
    bar._closed = True
    assert bar.fullscreen_active(mapping) is None
    user.GetForegroundWindow.assert_not_called()
    bar._on_fullscreen.assert_not_called()


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


def test_native_topmost_repair_ignores_stale_gtk_hint_and_waits_for_confirmation(
    monkeypatch,
):
    window = Mock()
    monkeypatch.setattr(appbar, "window_handle", lambda _window: 123)
    bar = appbar.WindowsAppBar.__new__(appbar.WindowsAppBar)
    bar._closed = False
    style = Mock(side_effect=[0x080800A0, 0x080800A8])
    bar._user = SimpleNamespace(GetWindowLongPtrW=style)
    bar._set_position = Mock(return_value=1)

    assert bar.ensure_keep_above(window) is False
    bar._set_position.assert_called_once_with(123, -1, 0, 0, 0, 0, 0x13)
    assert bar.ensure_keep_above(window) is True
    assert bar._set_position.call_count == 1
    style.assert_called_with(123, -20)
    window.present.assert_not_called()


@pytest.mark.parametrize("closed,hwnd", [(True, 123), (False, None)])
def test_native_topmost_repair_requires_a_live_owned_window(monkeypatch, closed, hwnd):
    monkeypatch.setattr(appbar, "window_handle", lambda _window: hwnd)
    bar = appbar.WindowsAppBar.__new__(appbar.WindowsAppBar)
    bar._closed = closed
    bar._user = Mock()
    bar._set_position = Mock()
    assert bar.ensure_keep_above(Mock()) is False
    bar._set_position.assert_not_called()
    assert bar._user.mock_calls == []
