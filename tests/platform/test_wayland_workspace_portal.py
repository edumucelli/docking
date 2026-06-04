"""Tests for Wayland workspace and portal services."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.platform.backends.base import ActionResult
from docking.platform.backends.wayland.portals import WaylandPortalColorPickerService
from docking.platform.backends.wayland.workspaces import (
    CAPABILITY_ACTIVATE,
    STATE_ACTIVE,
    WaylandWorkspaceService,
    load_workspace_protocol,
)


def _protocol() -> SimpleNamespace:
    return SimpleNamespace(
        start=MagicMock(),
        stop=MagicMock(),
        activate=MagicMock(),
    )


def test_wayland_workspace_service_tracks_workspaces_and_active_watchers():
    protocol = _protocol()
    service = WaylandWorkspaceService(protocol=protocol)
    changed: list[str] = []
    watch = service.watch_active_workspace(lambda: changed.append("changed"))
    handle_1 = object()
    handle_2 = object()

    service.workspace_created(handle_1)
    service.id_changed(handle_1, "workspace-a")
    service.name_changed(handle_1, "Code")
    service.capabilities_changed(handle_1, [CAPABILITY_ACTIVATE])
    service.state_changed(handle_1, [STATE_ACTIVE])
    service.workspace_created(handle_2)
    service.id_changed(handle_2, "workspace-b")
    service.name_changed(handle_2, "Chat")
    service.done()

    workspaces = service.list_workspaces()
    assert [workspace.id for workspace in workspaces] == ["workspace-a", "workspace-b"]
    assert [workspace.name for workspace in workspaces] == ["Code", "Chat"]
    assert workspaces[0].active is True
    assert service.active_workspace() == workspaces[0]
    assert changed == ["changed"]

    assert service.activate("workspace-a") is ActionResult.OK
    protocol.activate.assert_called_once_with(handle_1)

    service.unwatch_active_workspace(watch)
    service.state_changed(handle_1, [])
    service.state_changed(handle_2, [STATE_ACTIVE])
    service.done()
    assert changed == ["changed"]


def test_wayland_workspace_service_reports_unsupported_activate():
    service = WaylandWorkspaceService(protocol=_protocol())
    handle = object()
    service.workspace_created(handle)
    service.id_changed(handle, "workspace-a")
    service.done()

    assert service.activate("workspace-a") is ActionResult.UNSUPPORTED
    assert service.activate("missing") is ActionResult.NOT_FOUND


def test_wayland_workspace_service_accepts_protocol_bitfields():
    service = WaylandWorkspaceService(protocol=_protocol())
    handle = object()
    service.workspace_created(handle)
    service.id_changed(handle, "workspace-a")
    service.capabilities_changed(handle, 1)
    service.state_changed(handle, 1)
    service.done()

    active = service.active_workspace()
    assert active is not None
    assert active.id == "workspace-a"
    assert service.activate("workspace-a") is ActionResult.OK


def test_wayland_workspace_service_removes_workspaces():
    service = WaylandWorkspaceService(protocol=_protocol())
    handle = object()
    service.workspace_created(handle)
    service.id_changed(handle, "workspace-a")
    service.state_changed(handle, [STATE_ACTIVE])
    service.done()

    service.removed(handle)

    assert service.list_workspaces() == ()
    assert service.active_workspace() is None


def test_workspace_protocol_probe_is_safe_without_live_adapter():
    assert load_workspace_protocol() is None


def test_portal_color_picker_converts_float_channels_to_bytes():
    service = WaylandPortalColorPickerService(picker=lambda: (0.0, 0.5, 1.0))

    assert service.pick_color(x=10, y=20) == (0, 128, 255)


def test_portal_color_picker_clamps_channels_and_handles_cancel():
    service = WaylandPortalColorPickerService(picker=lambda: (-1.0, 2.0, 0.25))
    cancelled = WaylandPortalColorPickerService(picker=lambda: None)

    assert service.pick_color(x=10, y=20) == (0, 255, 64)
    assert cancelled.pick_color(x=10, y=20) is None
