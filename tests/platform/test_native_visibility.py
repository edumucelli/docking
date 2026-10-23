"""Overlap policy, conservative geometry and lifecycle regression coverage."""

import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from gi.repository import GLib

from docking.core.config import HideMode
from docking.platform.backends.base import DisplayServer, Rect, WindowId, WindowSnapshot
from docking.platform.backends.visibility import (
    SnapshotVisibilityMonitor,
    WindowChanges,
    should_hide,
)

DOCK = Rect(100, 680, 600, 40)
WINDOW = WindowSnapshot(
    id=WindowId(DisplayServer.WAYLAND, "fixture"),
    desktop_id="fixture.desktop",
    app_id="fixture",
    geometry=Rect(0, 0, 1280, 720),
    visible=True,
    active=True,
    minimized=False,
    pid=os.getpid() + 100,
)


@pytest.mark.parametrize(
    "mode", [HideMode.WINDOW_DODGE, HideMode.DODGE_ACTIVE, HideMode.INTELLIGENT]
)
def test_visible_native_window_overlaps(mode):
    assert should_hide(windows=[WINDOW], dock=DOCK, mode=mode)


@pytest.mark.parametrize(
    "changes",
    [
        {"visible": False},
        {"visible": None},
        {"minimized": True},
        {"geometry": None},
        {"geometry": Rect(0, 0, 1280, 600)},
        {"geometry": Rect(100, 680, 0, 40)},
        {"pid": os.getpid()},
    ],
)
def test_unknown_hidden_minimized_own_or_nonoverlapping_windows_never_hide(changes):
    assert not should_hide(
        windows=[replace(WINDOW, **changes)], dock=DOCK, mode=HideMode.WINDOW_DODGE
    )


def test_inactive_overlap_only_hides_any_window_mode():
    rows = [replace(WINDOW, active=False)]
    assert should_hide(windows=rows, dock=DOCK, mode=HideMode.WINDOW_DODGE)
    assert not should_hide(windows=rows, dock=DOCK, mode=HideMode.DODGE_ACTIVE)


def test_intelligent_uses_application_identity_not_title():
    focused = replace(WINDOW, geometry=Rect(0, 0, 200, 200))
    sibling = replace(
        WINDOW, id=WindowId(DisplayServer.WAYLAND, "sibling"), active=False
    )
    unrelated = replace(sibling, pid=42, desktop_id="other.desktop", app_id="other")
    assert should_hide(windows=[focused, sibling], dock=DOCK, mode=HideMode.INTELLIGENT)
    assert not should_hide(
        windows=[focused, unrelated], dock=DOCK, mode=HideMode.INTELLIGENT
    )


def test_maximized_mode_requires_authoritative_state_or_active_app_dialog():
    mode = HideMode.DODGE_MAXIMIZED
    assert not should_hide(windows=[WINDOW], dock=DOCK, mode=mode)
    assert should_hide(windows=[replace(WINDOW, maximized=True)], dock=DOCK, mode=mode)
    assert should_hide(windows=[replace(WINDOW, fullscreen=True)], dock=DOCK, mode=mode)
    dialog = replace(
        WINDOW, id=WindowId(DisplayServer.WAYLAND, "dialog"), active=False, dialog=True
    )
    assert should_hide(
        windows=[replace(WINDOW, geometry=Rect(0, 0, 100, 100)), dialog],
        dock=DOCK,
        mode=mode,
    )


def test_monitor_coalesces_without_restart_and_cleans_all_sources():
    changes = WindowChanges()
    windows = MagicMock()
    windows.watch.side_effect = changes.watch
    windows.unwatch.side_effect = changes.unwatch
    rows = [WINDOW]
    notify = MagicMock()
    config = SimpleNamespace(hide_mode_enum=HideMode.DODGE_ACTIVE)
    monitor = SnapshotVisibilityMonitor(
        windows=windows,
        visible_windows=lambda _rect: rows,
        config=config,
        get_dock_rect=lambda: DOCK,
        on_change=notify,
        poll_ms=0,
    )
    monitor.start()
    notify.assert_called_once_with(True)
    rows.clear()
    changes.notify()
    pending = monitor._pending
    for _ in range(100):
        changes.notify()
        assert monitor._pending == pending
    GLib.source_remove(pending)
    monitor._dispatch()
    notify.assert_called_with(False)
    changes.notify()
    pending = monitor._pending
    monitor.stop()
    assert GLib.MainContext.default().find_source_by_id(pending) is None
    changes.notify()
    assert monitor._pending == 0


def test_autohide_does_not_poll_compositor_windows():
    windows = MagicMock()
    windows.watch.return_value = None
    config = SimpleNamespace(hide_mode_enum=HideMode.AUTOHIDE)
    monitor = SnapshotVisibilityMonitor(
        windows=windows,
        visible_windows=lambda _rect: (),
        config=config,
        get_dock_rect=lambda: DOCK,
        on_change=MagicMock(),
        poll_ms=250,
    )
    monitor.start()
    assert monitor._poll()
    windows.refresh.assert_not_called()
    config.hide_mode_enum = HideMode.WINDOW_DODGE
    assert monitor._poll()
    windows.refresh.assert_called_once()
    monitor.stop()
