"""Windows physical geometry feeds the shared overlap policy in GTK space."""

import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.core.config import HideMode
from docking.platform.backends.base import DisplayServer, Rect, WindowId, WindowSnapshot
from docking.platform.backends.windows.appbar import ScreenMapping
from docking.platform.backends.windows.surface import WindowsSurfaceService
from docking.platform.backends.windows.visibility import WindowsVisibilityService


@pytest.mark.parametrize(
    "mode",
    [
        HideMode.DODGE_ACTIVE,
        HideMode.WINDOW_DODGE,
        HideMode.INTELLIGENT,
        HideMode.DODGE_MAXIMIZED,
    ],
)
def test_scaled_negative_monitor_overlap_and_minimize_use_projected_geometry(mode):
    surface = WindowsSurfaceService()
    surface._mapping = ScreenMapping(
        Rect(0, 0, 1280, 720), Rect(-2560, 0, 2560, 1440), Rect(-2560, 0, 2560, 1344), 2
    )
    row = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 100),
        desktop_id="fixture",
        app_id="fixture",
        pid=os.getpid() + 100,
        geometry=Rect(-2560, 0, 2560, 1344),
        active=True,
        visible=True,
        minimized=False,
        maximized=True,
    )
    windows = MagicMock()
    windows.list_all_windows.return_value = (row,)
    changed = MagicMock()
    service = WindowsVisibilityService(
        windows=windows, surface=surface, config=SimpleNamespace(hide_mode_enum=mode)
    )
    monitor = service.create_monitor(
        get_dock_rect=lambda: Rect(400, 620, 400, 50), on_change=changed
    )
    monitor.start()
    changed.assert_called_once_with(True)
    assert row.geometry == Rect(-2560, 0, 2560, 1344)
    windows.list_all_windows.return_value = (replace(row, minimized=True),)
    monitor.evaluate_now()
    changed.assert_called_with(False)
    service.stop()
    windows.unwatch.assert_called_once()


def test_unavailable_monitor_mapping_never_establishes_overlap():
    surface = WindowsSurfaceService()
    windows = MagicMock()
    windows.list_all_windows.return_value = (
        WindowSnapshot(
            id=WindowId(DisplayServer.WINDOWS, 100),
            desktop_id="fixture",
            geometry=Rect(0, 0, 2560, 1440),
            visible=True,
            active=True,
        ),
    )
    changed = MagicMock()
    service = WindowsVisibilityService(
        windows=windows,
        surface=surface,
        config=SimpleNamespace(hide_mode_enum=HideMode.WINDOW_DODGE),
    )
    monitor = service.create_monitor(
        get_dock_rect=lambda: Rect(400, 620, 400, 50), on_change=changed
    )
    monitor.evaluate_now()
    changed.assert_not_called()


def test_owned_dialog_participates_in_maximized_policy_without_taskbar_entry():
    surface = WindowsSurfaceService()
    surface._mapping = ScreenMapping(
        Rect(1280, 0, 1280, 720), Rect(0, 0, 2560, 1440), Rect(0, 0, 2560, 1344), 2
    )
    focused = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 100),
        desktop_id="fixture",
        app_id="fixture",
        pid=os.getpid() + 100,
        geometry=Rect(0, 0, 400, 400),
        visible=True,
        active=True,
        maximized=False,
    )
    dialog = replace(
        focused,
        id=WindowId(DisplayServer.WINDOWS, 101),
        active=False,
        dialog=True,
        skip_taskbar=True,
        geometry=Rect(600, 1100, 1000, 300),
    )
    windows = MagicMock()
    windows.list_all_windows.return_value = (focused, dialog)
    changed = MagicMock()
    service = WindowsVisibilityService(
        windows=windows,
        surface=surface,
        config=SimpleNamespace(hide_mode_enum=HideMode.DODGE_MAXIMIZED),
    )
    monitor = service.create_monitor(
        get_dock_rect=lambda: Rect(1680, 620, 400, 50), on_change=changed
    )
    monitor.evaluate_now()
    changed.assert_called_once_with(True)
    windows.list_all_windows.return_value = (
        focused,
        replace(dialog, pid=42, desktop_id="other", app_id="other"),
    )
    monitor.evaluate_now()
    changed.assert_called_with(False)
