"""AppBar dimensions stay in monitor space across DPI and negative origins."""

import ctypes
import os
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from docking.core.position import Position
from docking.platform.backends.base import MonitorSnapshot, Rect
from docking.platform.backends.windows import appbar
from docking.platform.backends.windows.appbar import edge_strip
from docking.platform.desktop.implementations.windows.geometry import (
    ScreenMapping,
    WindowsMonitorGeometry,
)


@pytest.fixture
def always_behind_stack(monkeypatch):
    monkeypatch.setattr(
        appbar.windows_ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False
    )
    monkeypatch.setattr(appbar, "window_handle", lambda _window: 100)
    state = {"handles": [10, 100, 20, 30], "topmost": set(), "hidden": set()}

    def enumerate_windows(callback, data):
        for handle in state["handles"]:
            assert callback(handle, data)
        return True

    def name(handle, buffer, _length):
        buffer.value = "WorkerW" if handle == 30 else "Application"
        return len(buffer.value)

    bar = appbar.WindowsAppBar.__new__(appbar.WindowsAppBar)
    bar._closed = False
    bar._set_position = Mock(return_value=True)
    bar._user = SimpleNamespace(
        EnumWindows=Mock(side_effect=enumerate_windows),
        GetClassNameW=Mock(side_effect=name),
        IsWindowVisible=Mock(side_effect=lambda handle: handle not in state["hidden"]),
        IsIconic=Mock(return_value=False),
        GetWindowLongPtrW=Mock(
            side_effect=lambda handle, _index: 8 if handle in state["topmost"] else 0
        ),
    )
    return bar, state


@pytest.mark.parametrize(
    "handles,sibling",
    [
        ([10, 100, 20, 30], 20),
        ([10, 20, 100, 30], None),
        ([10, 20, 30, 100], 20),
        ([30, 100], 0),
        ([100, 30], None),
    ],
)
def test_always_behind_dock_stays_below_applications_and_above_desktop(
    always_behind_stack, handles, sibling
):
    bar, state = always_behind_stack
    state["handles"] = handles
    assert bar.ensure_keep_below(Mock())
    if sibling is None:
        bar._set_position.assert_not_called()
    else:
        bar._set_position.assert_called_once_with(100, sibling, 0, 0, 0, 0, 0x13)


def test_always_behind_stacking_never_inherits_topmost_from_an_application(
    always_behind_stack,
):
    bar, state = always_behind_stack
    state["topmost"] = {20, 100}
    assert bar.ensure_keep_below(Mock())
    assert bar._set_position.call_args_list == [
        call(100, -2, 0, 0, 0, 0, 0x13),
        call(100, 10, 0, 0, 0, 0, 0x13),
    ]


def test_always_behind_stacking_ignores_hidden_applications(always_behind_stack):
    bar, state = always_behind_stack
    state["hidden"] = {20}
    assert bar.ensure_keep_below(Mock())
    bar._set_position.assert_not_called()


@pytest.mark.parametrize("failure", ["closed", "enumeration", "class-name"])
def test_always_behind_stacking_leaves_unknown_native_state_unchanged(
    always_behind_stack, failure
):
    bar, _state = always_behind_stack
    if failure == "closed":
        bar._closed = True
    elif failure == "enumeration":
        bar._user.EnumWindows.side_effect = None
        bar._user.EnumWindows.return_value = False
    else:
        bar._user.GetClassNameW.side_effect = None
        bar._user.GetClassNameW.return_value = 0
    assert not bar.ensure_keep_below(Mock())
    bar._set_position.assert_not_called()


def test_monitor_removed_before_native_mapping_is_reported_as_unavailable():
    geometry = WindowsMonitorGeometry.__new__(WindowsMonitorGeometry)
    geometry._monitor_from_point = Mock()
    geometry._monitor_info = Mock()
    display = Mock()
    display.get_primary_monitor.return_value = None
    display.get_monitor.return_value = None
    with pytest.raises(OSError, match="No monitor is available"):
        geometry.mapping(
            MonitorSnapshot(index=0, geometry=Rect(0, 0, 800, 600)), display
        )
    geometry._monitor_from_point.assert_not_called()
    geometry._monitor_info.assert_not_called()


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


def test_dock_activation_style_preserves_native_bits_and_allows_menu_focus(monkeypatch):
    monkeypatch.setattr(appbar, "window_handle", lambda _window: 123)
    monkeypatch.setattr(appbar.windows_ctypes, "set_last_error", Mock(), raising=False)
    layered_topmost = 0x00080008
    no_activate = 0x08000000
    bar = appbar.WindowsAppBar.__new__(appbar.WindowsAppBar)
    bar._closed = False
    bar._user = SimpleNamespace(
        GetWindowLongPtrW=Mock(
            side_effect=[layered_topmost, layered_topmost | no_activate]
        ),
        SetWindowLongPtrW=Mock(return_value=1),
    )
    bar._set_position = Mock()
    window = Mock()

    bar.set_dock_accept_focus(window, False)
    bar.set_dock_accept_focus(window, True)

    assert bar._user.SetWindowLongPtrW.call_args_list == [
        call(123, -20, layered_topmost | no_activate),
        call(123, -20, layered_topmost),
    ]
    assert bar._set_position.call_args_list == [call(123, None, 0, 0, 0, 0, 0x37)] * 2


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
    assert bar._set_position.call_args_list == [
        call(123, -2, 0, 0, 0, 0, 0x13),
        call(123, -1, 0, 0, 0, 0, 0x13),
    ]
    assert bar.ensure_keep_above(window) is True
    assert bar._set_position.call_count == 2
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


def test_native_topmost_repair_resets_a_successful_but_ineffective_promotion(
    monkeypatch,
):
    # Hosted Windows accepted repeated HWND_TOPMOST requests after fullscreen
    # but left WS_EX_TOPMOST off until an explicit HWND_NOTOPMOST transition.
    monkeypatch.setattr(appbar, "window_handle", lambda _window: 123)
    bar = appbar.WindowsAppBar.__new__(appbar.WindowsAppBar)
    bar._closed = False
    native_style = 0x080800A0
    reset = False

    def read_style(hwnd, index):
        assert (hwnd, index) == (123, -20)
        return native_style

    def position(hwnd, after, x, y, width, height, flags):
        nonlocal native_style, reset
        assert hwnd == 123
        assert (x, y, width, height, flags) == (0, 0, 0, 0, 0x13)
        if after == -2:
            reset = True
            native_style &= ~8
        elif after == -1 and reset:
            native_style |= 8
        return 1

    bar._user = SimpleNamespace(GetWindowLongPtrW=read_style)
    bar._set_position = position
    window = Mock()
    assert bar.ensure_keep_above(window) is False
    assert native_style & 8
    assert bar.ensure_keep_above(window) is True
    window.present.assert_not_called()


def test_native_topmost_repair_does_not_promote_after_reentrant_shutdown(monkeypatch):
    monkeypatch.setattr(appbar, "window_handle", lambda _window: 123)
    bar = appbar.WindowsAppBar.__new__(appbar.WindowsAppBar)
    bar._closed = False
    bar._user = SimpleNamespace(GetWindowLongPtrW=Mock(return_value=0))

    def shutdown(*_arguments):
        bar._closed = True
        return 1

    bar._set_position = Mock(side_effect=shutdown)
    assert bar.ensure_keep_above(Mock()) is False
    bar._set_position.assert_called_once_with(123, -2, 0, 0, 0, 0, 0x13)
