"""Tests for the GNOME Shell bridge backend."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.platform.backends.base import ActionResult, DisplayServer
from docking.platform.backends.gnome.bridge import (
    GnomeShellBridgeWindowService,
    GnomeShellBridgeWorkspaceService,
)


def _item(desktop_id: str, wm_class: str = "") -> SimpleNamespace:
    return SimpleNamespace(desktop_id=desktop_id, wm_class=wm_class)


def _launcher() -> SimpleNamespace:
    resolved = {
        "org.gnome.Nautilus.desktop": SimpleNamespace(
            desktop_id="org.gnome.Nautilus.desktop"
        ),
        "firefox.desktop": SimpleNamespace(desktop_id="firefox.desktop"),
    }
    aliases = {
        "firefox": SimpleNamespace(desktop_id="firefox.desktop"),
        "nautilus": SimpleNamespace(desktop_id="org.gnome.Nautilus.desktop"),
    }
    return SimpleNamespace(
        resolve=MagicMock(side_effect=lambda desktop_id, **_: resolved.get(desktop_id)),
        resolve_by_wm_class=MagicMock(
            side_effect=lambda wm_class: aliases.get(wm_class.lower())
        ),
    )


def _model(*items: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        visible_items=MagicMock(return_value=list(items)),
        update_running=MagicMock(),
    )


def _bridge() -> SimpleNamespace:
    return SimpleNamespace(
        list_windows=MagicMock(
            return_value=[
                {
                    "id": 7,
                    "title": "Files",
                    "app-id": "org.gnome.Nautilus",
                    "active": True,
                    "minimized": False,
                    "maximized": True,
                    "fullscreen": False,
                    "monitor": 0,
                    "workspace": 1,
                    "x": 10,
                    "y": 20,
                    "width": 800,
                    "height": 600,
                }
            ]
        ),
        list_workspaces=MagicMock(
            return_value=[
                {"id": 0, "index": 0, "name": "1", "active": False},
                {"id": 1, "index": 1, "name": "2", "active": True},
            ]
        ),
        activate=MagicMock(return_value=True),
        minimize=MagicMock(return_value=True),
        close=MagicMock(return_value=True),
        activate_workspace=MagicMock(return_value=True),
        subscribe_changed=MagicMock(return_value=99),
        unsubscribe_changed=MagicMock(),
    )


def test_gnome_shell_bridge_window_service_publishes_running_state_and_snapshots():
    model = _model(_item("org.gnome.Nautilus.desktop"))
    bridge = _bridge()
    service = GnomeShellBridgeWindowService(
        model=model,
        launcher=_launcher(),
        bridge=bridge,
    )

    service.refresh()

    running = model.update_running.call_args.kwargs["running"]
    info = running["org.gnome.Nautilus.desktop"]
    assert info.count == 1
    assert info.active is True

    snapshots = service.list_windows("org.gnome.Nautilus.desktop")
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.id.backend is DisplayServer.WAYLAND
    assert snapshot.id.value == "gnome:7"
    assert snapshot.title == "Files"
    assert snapshot.app_id == "org.gnome.Nautilus"
    assert snapshot.active is True
    assert snapshot.maximized is True
    assert snapshot.geometry.x == 10
    assert snapshot.geometry.width == 800
    assert snapshot.workspace_id == "1"
    assert snapshot.can_activate is True
    assert snapshot.can_close is True
    assert snapshot.can_minimize is True


def test_gnome_shell_bridge_window_service_actions_call_bridge():
    bridge = _bridge()
    service = GnomeShellBridgeWindowService(
        model=_model(_item("org.gnome.Nautilus.desktop")),
        launcher=_launcher(),
        bridge=bridge,
    )
    service.refresh()
    window_id = service.list_windows("org.gnome.Nautilus.desktop")[0].id

    assert service.activate(window_id) is ActionResult.OK
    assert service.activate_most_recent("org.gnome.Nautilus.desktop") is ActionResult.OK
    assert service.minimize_all("org.gnome.Nautilus.desktop") is ActionResult.OK
    assert service.close(window_id) is ActionResult.OK
    assert service.close_all("org.gnome.Nautilus.desktop") is ActionResult.OK
    assert service.close_focused("org.gnome.Nautilus.desktop") is ActionResult.OK

    bridge.activate.assert_any_call(7)
    bridge.minimize.assert_any_call(7)
    bridge.close.assert_any_call(7)


def test_gnome_shell_bridge_window_service_returns_not_found_for_unknown_window():
    service = GnomeShellBridgeWindowService(
        model=_model(),
        launcher=_launcher(),
        bridge=_bridge(),
    )

    assert service.activate_most_recent("missing.desktop") is ActionResult.NOT_FOUND
    assert (
        service.activate(SimpleNamespace(backend=DisplayServer.X11, value=1))
        is ActionResult.NOT_FOUND
    )


def test_gnome_shell_bridge_workspace_service_lists_and_activates_workspaces():
    bridge = _bridge()
    service = GnomeShellBridgeWorkspaceService(bridge=bridge)

    service.refresh()

    workspaces = service.list_workspaces()
    assert len(workspaces) == 2
    assert service.active_workspace().id == "1"
    assert service.activate("1") is ActionResult.OK
    bridge.activate_workspace.assert_called_once_with("1")


def test_gnome_shell_bridge_workspace_watchers_are_called_on_refresh():
    bridge = _bridge()
    service = GnomeShellBridgeWorkspaceService(bridge=bridge)
    callback = MagicMock()
    handle = service.watch_active_workspace(callback)

    service.refresh()
    service.refresh()
    assert callback.call_count == 1

    bridge.list_workspaces.return_value = [
        {"id": 0, "index": 0, "name": "1", "active": True},
        {"id": 1, "index": 1, "name": "2", "active": False},
    ]
    service.refresh()
    service.unwatch_active_workspace(handle)
    service.refresh()

    assert callback.call_count == 2
