"""Tests for COSMIC-specific Wayland protocol composition."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.core.items import DockItem
from docking.platform.backends.base import Rect
from docking.platform.backends.wayland.cosmic import (
    CosmicOverlapAdapter,
    CosmicToplevelAdapter,
)
from docking.platform.backends.wayland.cosmic_session import (
    CosmicOverlapVisibilityService,
    CosmicSessionBackend,
)
from docking.platform.backends.wayland.runtime import WaylandProtocolRuntime
from docking.platform.backends.wayland.toplevels import (
    WaylandForeignToplevelWindowService,
)
from docking.platform.backends.wayland.workspaces import WaylandWorkspaceService
from tests.platform.application_fakes import application, identity_services


class _Handle:
    def __init__(self) -> None:
        self.dispatcher: dict[str, object] = {}


def _model() -> SimpleNamespace:
    return SimpleNamespace(
        visible_items=MagicMock(
            return_value=[DockItem(desktop_id="foot.desktop", wm_class="foot")]
        ),
        update_running=MagicMock(),
    )


def test_cosmic_info_batch_publishes_state_geometry_and_workspace() -> None:
    model = _model()
    adapter = CosmicToplevelAdapter()
    adapter.set_output_origin_probe(lambda _: (1920, 0))
    adapter.set_workspace_id_probe(lambda workspace: workspace.id)
    service = WaylandForeignToplevelWindowService(
        model=model,
        **identity_services(application("foot.desktop", wm_class="foot")),
        protocol=adapter,
    )
    adapter.start(service)
    toplevel = _Handle()
    adapter._on_toplevel(None, toplevel)
    toplevel.dispatcher["title"](toplevel, "Terminal")
    toplevel.dispatcher["app_id"](toplevel, "foot")
    toplevel.dispatcher["done"](toplevel)
    model.update_running.reset_mock()

    workspace = SimpleNamespace(id="workspace-2")
    adapter._on_cosmic_state(toplevel, [2, 3])
    adapter._on_cosmic_geometry(toplevel, object(), 10, 20, 800, 600)
    adapter._on_cosmic_workspace_enter(toplevel, workspace)

    model.update_running.assert_not_called()
    adapter._on_info_done(None)

    model.update_running.assert_called_once()
    snapshot = service.list_all_windows()[0]
    assert snapshot.active is True
    assert snapshot.fullscreen is True
    assert snapshot.geometry == Rect(x=1930, y=20, width=800, height=600)
    assert snapshot.workspace_id == "workspace-2"

    adapter._on_cosmic_workspace_leave(toplevel, workspace)
    adapter._on_info_done(None)
    assert service.list_all_windows()[0].workspace_id is None


def test_cosmic_toplevel_close_releases_both_handle_mappings() -> None:
    adapter = CosmicToplevelAdapter()
    toplevel = _Handle()
    cosmic_handle = _Handle()
    adapter._pending_toplevels.append(toplevel)
    adapter._cosmic_handles[toplevel] = cosmic_handle
    adapter._ext_handles[cosmic_handle] = toplevel

    adapter._on_toplevel_closed(toplevel)

    assert adapter._cosmic_handles == {}
    assert adapter._ext_handles == {}


def test_cosmic_geometry_only_update_is_published_at_batch_completion() -> None:
    adapter = CosmicToplevelAdapter()
    adapter.set_output_origin_probe(lambda _: (1920, -100))
    adapter._service = MagicMock()
    toplevel = _Handle()

    adapter._on_cosmic_geometry(toplevel, object(), 10, 20, 800, 600)
    adapter._service.geometry_changed.assert_not_called()
    adapter._service.done.assert_not_called()
    adapter._on_info_done(None)

    adapter._service.geometry_changed.assert_called_once_with(
        toplevel, Rect(1930, -80, 800, 600)
    )
    adapter._service.done.assert_called_once_with(toplevel)


def test_cosmic_unknown_location_is_not_guessed_from_proxy_ids() -> None:
    adapter = CosmicToplevelAdapter()
    adapter._service = MagicMock()
    toplevel = _Handle()
    workspace = SimpleNamespace(id=72)

    adapter._on_cosmic_geometry(toplevel, object(), 10, 20, 800, 600)
    adapter._on_cosmic_workspace_enter(toplevel, workspace)
    adapter._on_info_done(None)

    adapter._service.geometry_changed.assert_called_once_with(toplevel, None)
    adapter._service.workspace_changed.assert_called_once_with(toplevel, None)


def test_cosmic_runtime_keeps_legacy_decoder_types_but_binds_ext_workspace() -> None:
    pytest.importorskip("pywayland")
    from docking.platform.backends.wayland.protocols.cosmic_toplevel_info_v1 import (
        ZcosmicToplevelHandleV1,
    )
    from docking.platform.backends.wayland.protocols.cosmic_workspace_v1 import (
        ZcosmicWorkspaceHandleV1,
    )
    from docking.platform.backends.wayland.protocols.ext_workspace_v1 import (
        ExtWorkspaceManagerV1,
    )

    event = next(
        m for m in ZcosmicToplevelHandleV1.events if m.name == "workspace_enter"
    )
    assert event.arguments[0].interface is ZcosmicWorkspaceHandleV1
    runtime = WaylandProtocolRuntime()
    registry = MagicMock()
    runtime._on_global(registry, 10, "zcosmic_workspace_manager_v2", 2)
    registry.bind.assert_not_called()
    runtime._on_global(registry, 11, "ext_workspace_manager_v1", 1)
    registry.bind.assert_called_once_with(11, ExtWorkspaceManagerV1, 1)


@pytest.mark.parametrize("protocol_id", [None, "workspace-2"])
def test_cosmic_runtime_uses_the_workspace_service_snapshot_id(protocol_id) -> None:
    runtime = WaylandProtocolRuntime()
    workspace = _Handle()
    workspace.id = 72  # A Wayland object ID is not a workspace snapshot ID.
    runtime.workspaces._on_workspace(None, workspace)
    if protocol_id is not None:
        runtime.workspaces._on_workspace_id(workspace, protocol_id)
    service = WaylandWorkspaceService(protocol=runtime.workspaces)
    service.start()
    adapter = runtime.cosmic_toplevel
    adapter._service = MagicMock()
    toplevel = _Handle()
    adapter._on_cosmic_workspace_enter(toplevel, workspace)
    adapter._on_info_done(None)

    adapter._service.workspace_changed.assert_called_once_with(
        toplevel, service.list_workspaces()[0].id
    )


def test_cosmic_multiple_workspace_membership_is_not_reduced_to_last_enter() -> None:
    adapter = CosmicToplevelAdapter()
    adapter.set_workspace_id_probe(lambda workspace: workspace.id)
    adapter._service = MagicMock()
    toplevel = _Handle()
    first = SimpleNamespace(id="workspace-1")
    second = SimpleNamespace(id="workspace-2")
    adapter._on_cosmic_workspace_enter(toplevel, first)
    adapter._on_cosmic_workspace_enter(toplevel, second)
    adapter._on_info_done(None)
    adapter._service.workspace_changed.assert_called_once_with(toplevel, None)

    adapter._on_cosmic_workspace_leave(toplevel, second)
    adapter._on_info_done(None)
    adapter._service.workspace_changed.assert_called_with(toplevel, "workspace-1")


def test_cosmic_runtime_resolves_bound_output_origin() -> None:
    pytest.importorskip("pywayland")
    runtime = WaylandProtocolRuntime()
    output = _Handle()
    registry = SimpleNamespace(bind=MagicMock(return_value=output))
    runtime._on_global(registry, 10, "wl_output", 4)
    assert runtime.treeland_overlap.output_origin(output) is None
    output.dispatcher["geometry"](output, 1920, -100, 600, 340, 0, "Vendor", "Model", 0)
    adapter = runtime.cosmic_toplevel
    adapter._service = MagicMock()
    toplevel = _Handle()
    adapter._on_cosmic_geometry(toplevel, output, 10, 20, 800, 600)
    adapter._on_info_done(None)
    adapter._service.geometry_changed.assert_called_once_with(
        toplevel, Rect(1930, -80, 800, 600)
    )
    runtime._on_global_remove(registry, 10)
    assert runtime.treeland_overlap.output_origin(output) is None


def test_cosmic_overlap_attaches_when_surface_precedes_monitor() -> None:
    adapter = SimpleNamespace(
        start=MagicMock(),
        stop=MagicMock(),
        evaluate_now=MagicMock(),
    )
    service = CosmicOverlapVisibilityService(overlap_adapter=adapter)
    layer_surface = object()
    service.attach_layer_surface(layer_surface)

    monitor = service.create_monitor(
        get_dock_rect=lambda: None,
        on_change=MagicMock(),
    )
    assert monitor is not None
    adapter.start.assert_not_called()

    monitor.start()
    adapter.start.assert_called_once_with(layer_surface, monitor._on_change)


def test_cosmic_overlap_stop_clears_availability() -> None:
    adapter = CosmicOverlapAdapter()
    adapter.available = True
    adapter._notification = SimpleNamespace(destroy=MagicMock())

    adapter.stop()

    assert adapter.available is False
    assert adapter._notification is None


@pytest.mark.parametrize("info_version", [0, 2, 3])
@pytest.mark.parametrize("workspace_available", [False, True])
def test_cosmic_session_reports_only_delivered_toplevel_capabilities(
    info_version, workspace_available
) -> None:
    toplevel_adapter = CosmicToplevelAdapter()
    toplevel_adapter._toplevel_info = object() if info_version else None
    toplevel_adapter._toplevel_info_version = info_version
    runtime = SimpleNamespace(
        cosmic_toplevel_protocol=toplevel_adapter,
        cosmic_overlap_protocol=None,
        preview_protocol=None,
        hyprland_preview_protocol=None,
        foreign_toplevel_protocol=None,
        workspace_protocol=SimpleNamespace() if workspace_available else None,
        idle_protocol=None,
        stop=MagicMock(),
    )
    backend = CosmicSessionBackend(
        layer_shell=SimpleNamespace(),
        model=_model(),
        **identity_services(application("foot.desktop", wm_class="foot")),
        protocol_runtime=runtime,
        screen_capture=MagicMock(),
    )

    assert backend.capabilities.tracks_window_geometry is (info_version >= 2)
    assert backend.capabilities.tracks_window_workspace is (
        info_version >= 3 and workspace_available
    )
    assert (backend.workspaces is not None) is workspace_available
