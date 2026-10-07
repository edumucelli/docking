"""Tests for generic X11 external panel workarea composition."""

import ctypes
from unittest.mock import MagicMock

from docking.platform.backends.base import Rect
from docking.platform.backends.x11.impl import workarea as workarea_mod
from docking.platform.backends.x11.impl.workarea import (
    ExternalWorkareaTracker,
    StrutReservation,
    compute_external_workarea,
    normalize_strut,
)


def _reservation(window_id: int, values: tuple[int, ...]) -> StrutReservation:
    return StrutReservation(window_id=window_id, values=values)


def _partial(
    *,
    left: int = 0,
    right: int = 0,
    top: int = 0,
    bottom: int = 0,
    left_span: tuple[int, int] = (0, 1079),
    right_span: tuple[int, int] = (0, 1079),
    top_span: tuple[int, int] = (0, 1919),
    bottom_span: tuple[int, int] = (0, 1919),
) -> tuple[int, ...]:
    return (
        left,
        right,
        top,
        bottom,
        *left_span,
        *right_span,
        *top_span,
        *bottom_span,
    )


def test_no_external_panel_preserves_full_monitor_geometry():
    monitor = Rect(0, 0, 1920, 1080)

    result = compute_external_workarea(
        monitor=monitor,
        screen_width=1920,
        screen_height=1080,
        scale=1,
        reservations=[],
    )

    assert result == monitor


def test_panels_on_all_edges_compose_by_root_boundary():
    result = compute_external_workarea(
        monitor=Rect(0, 0, 1920, 1080),
        screen_width=1920,
        screen_height=1080,
        scale=1,
        reservations=[
            _reservation(1, _partial(top=28)),
            _reservation(2, _partial(bottom=40)),
            _reservation(3, _partial(left=32)),
            _reservation(4, _partial(right=24)),
        ],
    )

    assert result == Rect(32, 28, 1864, 1012)


def test_larger_same_edge_boundary_wins_without_summing_panels():
    result = compute_external_workarea(
        monitor=Rect(0, 0, 1920, 1080),
        screen_width=1920,
        screen_height=1080,
        scale=1,
        reservations=[
            _reservation(1, _partial(top=28)),
            _reservation(2, _partial(top=64)),
        ],
    )

    assert result == Rect(0, 64, 1920, 1016)


def test_partial_panel_only_affects_intersecting_monitor():
    panel = _reservation(1, _partial(top=30, top_span=(1920, 3839)))

    left = compute_external_workarea(
        monitor=Rect(0, 0, 1920, 1080),
        screen_width=3840,
        screen_height=1080,
        scale=1,
        reservations=[panel],
    )
    right = compute_external_workarea(
        monitor=Rect(1920, 0, 1920, 1080),
        screen_width=3840,
        screen_height=1080,
        scale=1,
        reservations=[panel],
    )

    assert left == Rect(0, 0, 1920, 1080)
    assert right == Rect(1920, 30, 1920, 1050)


def test_physical_strut_rounds_inward_at_fractional_logical_boundary():
    result = compute_external_workarea(
        monitor=Rect(0, 0, 960, 540),
        screen_width=1920,
        screen_height=1080,
        scale=2,
        reservations=[_reservation(1, _partial(top=55))],
    )

    assert result == Rect(0, 28, 960, 512)


def test_legacy_strut_expands_to_full_root_span():
    values = normalize_strut(
        (0, 0, 28, 0),
        screen_width=1920,
        screen_height=1080,
    )

    assert values == _partial(top=28)


def test_malformed_strut_is_ignored():
    assert normalize_strut((1, 2, 3), screen_width=1920, screen_height=1080) is None


def test_destroyed_client_badwindow_is_trapped():
    display = MagicMock()
    display.error_trap_pop.return_value = 3
    xlib = MagicMock()
    xlib.XGetWindowAttributes.return_value = 1
    tracker = ExternalWorkareaTracker()
    tracker._gdk_display = display
    tracker._xdisplay = ctypes.c_void_p(1)
    tracker._xlib = xlib

    mapped = tracker._is_mapped(999)

    assert mapped is False
    display.error_trap_push.assert_called_once_with()
    display.error_trap_pop.assert_called_once_with()


def test_configure_events_only_refresh_tracked_strut_clients(monkeypatch):
    idle_add = MagicMock(return_value=77)
    monkeypatch.setattr(workarea_mod.GLib, "idle_add", idle_add)
    tracker = ExternalWorkareaTracker()
    tracker._root = 1
    event = workarea_mod._XStructureEvent(
        type=workarea_mod._CONFIGURE_NOTIFY,
        event=1,
        window=42,
    )

    tracker._event_filter(ctypes.addressof(event), 0, 0)
    idle_add.assert_not_called()

    tracker._tracked_windows[42] = object()
    tracker._event_filter(ctypes.addressof(event), 0, 0)

    idle_add.assert_called_once_with(tracker._apply_refresh)


def test_gdk_filter_callback_contains_python_exceptions():
    tracker = ExternalWorkareaTracker()
    tracker._event_filter = MagicMock(side_effect=RuntimeError("broken event"))

    result = tracker._gdk_filter_callback(1, 0, 0)

    assert result == workarea_mod._GDK_FILTER_CONTINUE
