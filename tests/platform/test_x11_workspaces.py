"""Tests for X11 workspace service."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.platform.backends.base import ActionResult
from docking.platform.backends.x11.services import workspaces
from docking.platform.backends.x11.services.workspaces import (
    WnckWorkspaceService,
    WorkspaceWatchHandle,
)


def _workspace(number: int, name: str = ""):
    workspace = MagicMock()
    workspace.get_number.return_value = number
    workspace.get_name.return_value = name
    return workspace


def test_list_workspaces_returns_snapshots(monkeypatch):
    first = _workspace(0, "One")
    second = _workspace(1, "Two")
    screen = MagicMock()
    screen.get_workspaces.return_value = [first, second]
    screen.get_active_workspace.return_value = second
    monkeypatch.setattr(workspaces.Wnck.Screen, "get_default", lambda: screen)

    snapshots = WnckWorkspaceService().list_workspaces()

    assert [snapshot.id for snapshot in snapshots] == ["0", "1"]
    assert snapshots[1].active is True
    screen.force_update.assert_called_once_with()


def test_activate_workspace(monkeypatch):
    target = MagicMock()
    screen = MagicMock()
    screen.get_workspace.return_value = target
    monkeypatch.setattr(workspaces.Wnck.Screen, "get_default", lambda: screen)
    monkeypatch.setattr(workspaces.Gtk, "get_current_event_time", lambda: 99)

    result = WnckWorkspaceService().activate("2")

    assert result is ActionResult.OK
    screen.get_workspace.assert_called_once_with(2)
    target.activate.assert_called_once_with(99)


def test_watch_and_unwatch_active_workspace(monkeypatch):
    screen = MagicMock()
    screen.connect.return_value = 33
    monkeypatch.setattr(workspaces.Wnck.Screen, "get_default", lambda: screen)
    callback = MagicMock()
    service = WnckWorkspaceService()

    handle = service.watch_active_workspace(callback)

    assert handle == WorkspaceWatchHandle(screen=screen, signal_id=33)
    signal_name, handler = screen.connect.call_args.args
    assert signal_name == "active-workspace-changed"
    handler(screen)
    callback.assert_called_once_with()

    service.unwatch_active_workspace(handle)
    screen.disconnect.assert_called_once_with(33)
