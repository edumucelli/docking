"""Tests for COSMIC-specific Wayland protocol composition."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.core.config import Config, HideMode
from docking.core.items import DockItem
from docking.platform.backends.base import Rect
from docking.platform.backends.visibility import SnapshotVisibilityService
from docking.platform.backends.wayland.cosmic import (
    CosmicOverlapAdapter,
    CosmicToplevelAdapter,
)
from docking.platform.backends.wayland.cosmic_session import (
    CosmicOverlapVisibilityService,
    CosmicSessionBackend,
    CosmicWindowService,
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
    adapter._service.done_batch.assert_called_once_with((toplevel,))


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


@pytest.mark.parametrize("protocol_id", [None, "workspace-2"])
def test_cosmic_session_replays_initial_windows_with_workspace_ids(protocol_id) -> None:
    runtime = WaylandProtocolRuntime()
    runtime.workspaces.available = True
    runtime.cosmic_toplevel.available = True
    workspace = _Handle()
    runtime.workspaces._on_workspace(None, workspace)
    if protocol_id is not None:
        runtime.workspaces._on_workspace_id(workspace, protocol_id)
    toplevel = _Handle()
    runtime.cosmic_toplevel._on_toplevel(None, toplevel)
    toplevel.dispatcher["app_id"](toplevel, "foot")
    runtime.cosmic_toplevel._on_cosmic_workspace_enter(toplevel, workspace)
    toplevel.dispatcher["done"](toplevel)
    backend = CosmicSessionBackend(
        layer_shell=SimpleNamespace(),
        model=_model(),
        **identity_services(application("foot.desktop", wm_class="foot")),
        protocol_runtime=runtime,
        screen_capture=MagicMock(),
    )

    backend.start()

    workspace_snapshot = backend.workspaces.list_workspaces()[0]
    window_snapshot = backend.windows.list_all_windows()[0]
    assert workspace_snapshot.id == (protocol_id or "0")
    assert window_snapshot.workspace_id == workspace_snapshot.id


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
    assert backend.capabilities.supports_current_workspace_filter is (
        info_version >= 3 and workspace_available
    )
    assert backend.capabilities.supports_overlap_any is (
        info_version >= 3 and workspace_available
    )
    assert backend.capabilities.supports_overlap_active is (
        info_version >= 3 and workspace_available
    )
    assert (backend.workspaces is not None) is workspace_available


@pytest.mark.parametrize("info_version", [0, 1, 2, 3])
@pytest.mark.parametrize("management", [False, True])
def test_cosmic_session_does_not_claim_unavailable_state_or_actions(
    info_version, management
) -> None:
    runtime = WaylandProtocolRuntime()
    adapter = runtime.cosmic_toplevel
    adapter.available = True
    adapter._toplevel_info = object() if info_version else None
    adapter._toplevel_info_version = info_version
    adapter._toplevel_manager = MagicMock() if management else None
    adapter._capabilities = {1, 2}  # Close and activate; minimization denied.
    adapter._seat = object()
    backend = CosmicSessionBackend(
        layer_shell=SimpleNamespace(),
        model=_model(),
        **identity_services(),
        protocol_runtime=runtime,
        screen_capture=MagicMock(),
    )
    caps = backend.capabilities
    assert caps.tracks_windows is True
    assert caps.tracks_active_window is (info_version >= 2)
    assert caps.tracks_minimized is (info_version >= 2)
    assert caps.supports_activate is (management and info_version >= 2)
    assert caps.supports_close is (management and info_version >= 2)
    assert caps.supports_minimize is False


def test_cosmic_management_requires_a_cosmic_handle_and_an_advertised_action():
    adapter = CosmicToplevelAdapter()
    adapter._toplevel_manager = MagicMock()
    adapter._toplevel_info = object()
    adapter._toplevel_info_version = 2
    adapter._capabilities = {1, 2, 4}
    adapter._seat = object()
    toplevel, cosmic_handle = _Handle(), _Handle()
    assert adapter.supports_action("activate", toplevel) is False
    adapter._cosmic_handles[toplevel] = cosmic_handle
    assert adapter.supports_action("activate", toplevel) is True
    adapter._seat = None
    assert adapter.supports_action("activate", toplevel) is False
    assert adapter.supports_action("close", toplevel) is True
    adapter._capabilities = set()
    assert adapter.supports_action("close", toplevel) is False


@pytest.mark.parametrize("version", [1, 2, 3])
def test_cosmic_info_request_requires_version_two_and_is_not_duplicated(version):
    adapter = CosmicToplevelAdapter()
    adapter._toplevel_info_version = version
    get_info = MagicMock(return_value=_Handle())
    flush = MagicMock()
    adapter.set_flush_callback(flush)
    adapter._toplevel_info = SimpleNamespace(get_cosmic_toplevel=get_info)
    toplevel = _Handle()
    adapter._request_cosmic_info(toplevel)
    adapter._request_cosmic_info(toplevel)
    assert get_info.call_count == (1 if version >= 2 else 0)
    assert flush.call_count == (1 if version >= 2 else 0)


def _native_cosmic_backend(*, mode=HideMode.WINDOW_DODGE, current_only=False):
    runtime = WaylandProtocolRuntime()
    runtime.workspaces.available = True
    adapter = runtime.cosmic_toplevel
    adapter.available = True
    adapter._toplevel_info = SimpleNamespace()
    adapter._toplevel_info_version = 3
    adapter.set_output_origin_probe(lambda _: (0, 0))
    config = Config()
    config.hide_mode = mode.value
    config.current_workspace_only = current_only
    model = _model()
    backend = CosmicSessionBackend(
        layer_shell=SimpleNamespace(),
        model=model,
        config=config,
        **identity_services(application("foot.desktop", wm_class="foot")),
        protocol_runtime=runtime,
        screen_capture=MagicMock(),
    )
    workspaces = [_Handle(), _Handle(), _Handle()]
    for index, workspace in enumerate(workspaces):
        runtime.workspaces._on_workspace(None, workspace)
        runtime.workspaces._on_workspace_id(workspace, f"workspace-{index}")
        runtime.workspaces._on_workspace_state(workspace, 1 if index < 2 else 0)
    backend.start()
    runtime.workspaces._on_done(None)
    return runtime, backend, model, workspaces


def _cosmic_window(runtime, workspace, states=(2,), geometry=None):
    geometry = geometry or Rect(0, 650, 800, 70)
    adapter = runtime.cosmic_toplevel
    handle = _Handle()
    adapter._on_toplevel(None, handle)
    adapter._on_toplevel_app_id(handle, "foot")
    adapter._on_cosmic_workspace_enter(handle, workspace)
    adapter._on_cosmic_state(handle, states)
    output = object()
    adapter._on_cosmic_output(handle, output, entered=True)
    adapter._on_cosmic_geometry(
        handle, output, geometry.x, geometry.y, geometry.width, geometry.height
    )
    adapter._on_info_done(None)
    return handle, output


@pytest.mark.parametrize(
    "mode,states,hidden",
    [
        (HideMode.WINDOW_DODGE, (), True),
        (HideMode.DODGE_ACTIVE, (), False),
        (HideMode.DODGE_ACTIVE, (2,), True),
        (HideMode.INTELLIGENT, (2,), True),
        (HideMode.DODGE_MAXIMIZED, (2,), False),
        (HideMode.DODGE_MAXIMIZED, (0, 2), True),
        (HideMode.DODGE_MAXIMIZED, (2, 3), True),
    ],
)
def test_cosmic_native_visibility_obeys_each_policy(mode, states, hidden):
    runtime, backend, _, workspaces = _native_cosmic_backend(mode=mode)
    try:
        _cosmic_window(runtime, workspaces[0], states=states)
        assert isinstance(backend.visibility, SnapshotVisibilityService)
        assert backend.capabilities.supports_overlap_active
        assert backend.capabilities.supports_overlap_any
        assert backend.capabilities.supports_overlap_maximized
        changes = []
        monitor = backend.visibility.create_monitor(
            get_dock_rect=lambda: Rect(300, 680, 500, 40),
            on_change=changes.append,
        )
        monitor.evaluate_now()
        assert changes == ([True] if hidden else [])
    finally:
        backend.stop()


def test_cosmic_workspace_switch_updates_filter_and_visibility_on_second_output():
    runtime, backend, model, workspaces = _native_cosmic_backend(current_only=True)
    try:
        handle, output = _cosmic_window(runtime, workspaces[1])
        assert isinstance(backend.windows, CosmicWindowService)
        assert backend.capabilities.supports_current_workspace_filter
        assert len(backend.windows.list_windows("foot.desktop")) == 1
        assert backend.windows.list_all_windows()[0].visible is True
        changes = []
        monitor = backend.visibility.create_monitor(
            get_dock_rect=lambda: Rect(300, 680, 500, 40),
            on_change=changes.append,
        )
        monitor.evaluate_now()
        assert changes == [True]
        model.update_running.reset_mock()
        runtime.workspaces._on_workspace_state(workspaces[1], 0)
        runtime.workspaces._on_workspace_state(workspaces[2], 1)
        runtime.workspaces._on_done(None)
        monitor.evaluate_now()
        assert changes == [True, False]
        assert backend.windows.list_windows("foot.desktop") == ()
        assert len(backend.windows.list_all_windows()) == 1
        assert backend.windows.list_all_windows()[0].visible is False
        model.update_running.assert_called_once_with(running={})
        # Multi-workspace membership and sticky windows remain present.
        runtime.cosmic_toplevel._on_cosmic_workspace_enter(handle, workspaces[2])
        runtime.cosmic_toplevel._on_info_done(None)
        assert backend.windows.list_all_windows()[0].visible is True
        runtime.cosmic_toplevel._on_cosmic_workspace_leave(handle, workspaces[2])
        runtime.cosmic_toplevel._on_cosmic_state(handle, (4,))
        runtime.cosmic_toplevel._on_info_done(None)
        assert backend.windows.list_all_windows()[0].sticky is True
        assert len(backend.windows.list_windows("foot.desktop")) == 1
        # Leaving every visible output must remove an otherwise sticky overlap.
        runtime.cosmic_toplevel._on_cosmic_output(handle, output, entered=False)
        runtime.cosmic_toplevel._on_info_done(None)
        assert backend.windows.list_all_windows()[0].visible is False
    finally:
        backend.stop()


def test_cosmic_unknown_workspace_never_hides_dock_and_cannot_cross_gtk_connections():
    runtime, backend, _, _ = _native_cosmic_backend()
    try:
        _cosmic_window(runtime, _Handle())
        assert backend.windows.list_all_windows()[0].visible is None
        changes = []
        monitor = backend.visibility.create_monitor(
            get_dock_rect=lambda: Rect(300, 680, 500, 40),
            on_change=changes.append,
        )
        monitor.evaluate_now()
        assert changes == []
        assert backend.surface._on_layer_surface_ready is None
    finally:
        backend.stop()


def test_cosmic_raw_overlap_global_does_not_claim_gtk_dodge_without_window_geometry():
    runtime = WaylandProtocolRuntime()
    runtime.cosmic_overlap.available = True
    backend = CosmicSessionBackend(
        layer_shell=SimpleNamespace(),
        model=_model(),
        **identity_services(),
        protocol_runtime=runtime,
        screen_capture=MagicMock(),
    )
    assert backend.capabilities.supports_any_overlap is False
    assert backend.surface._on_layer_surface_ready is None
    backend.stop()


def test_cosmic_batch_notifies_once_and_unwatch_releases_callbacks():
    runtime, backend, model, workspaces = _native_cosmic_backend()
    try:
        first, output = _cosmic_window(runtime, workspaces[0])
        second, _ = _cosmic_window(runtime, workspaces[0])
        changes = MagicMock()
        watch = backend.windows.watch(changes)
        model.update_running.reset_mock()
        runtime.cosmic_toplevel._on_cosmic_state(first, ())
        runtime.cosmic_toplevel._on_cosmic_geometry(second, output, 10, 20, 100, 100)
        changes.assert_not_called()
        runtime.cosmic_toplevel._on_info_done(None)
        changes.assert_called_once_with()
        model.update_running.assert_called_once()
        backend.windows.unwatch(watch)
        runtime.cosmic_toplevel._on_toplevel_closed(first)
        changes.assert_called_once_with()
    finally:
        backend.stop()


def test_cosmic_geometry_survives_unknown_second_output_and_clears_departed_output():
    adapter = CosmicToplevelAdapter()
    first, second, toplevel = _Handle(), _Handle(), _Handle()
    adapter._service = MagicMock()
    adapter.set_output_origin_probe(
        lambda output: (1920, 0) if output is first else None
    )
    adapter._on_cosmic_geometry(toplevel, first, -100, 20, 400, 300)
    adapter._on_cosmic_geometry(toplevel, second, 1820, 20, 400, 300)
    adapter._on_info_done(None)
    adapter._service.geometry_changed.assert_called_once_with(
        toplevel, Rect(1820, 20, 400, 300)
    )
    adapter._on_cosmic_output(toplevel, first, entered=False)
    adapter._on_info_done(None)
    adapter._service.geometry_changed.assert_called_with(toplevel, None)


def test_cosmic_close_destroys_native_proxies_once():
    adapter = CosmicToplevelAdapter()
    toplevel, cosmic = _Handle(), _Handle()
    toplevel.destroy = MagicMock()
    cosmic.destroy = MagicMock()
    adapter._pending_toplevels.append(toplevel)
    adapter._cosmic_handles[toplevel] = cosmic
    adapter._ext_handles[cosmic] = toplevel
    adapter._on_toplevel_closed(toplevel)
    adapter.stop()
    toplevel.destroy.assert_called_once_with()
    cosmic.destroy.assert_called_once_with()


@pytest.mark.parametrize(
    "action", ["activate", "close", "set_minimized", "unset_minimized"]
)
def test_cosmic_actions_never_send_a_standard_handle_to_the_manager(action):
    adapter = CosmicToplevelAdapter()
    adapter._toplevel_info = object()
    adapter._toplevel_info_version = 2
    adapter._toplevel_manager = manager = MagicMock()
    adapter._seat = object()
    adapter._capabilities = {1, 2, 4}
    toplevel, cosmic_handle = _Handle(), _Handle()
    getattr(adapter, action)(toplevel)
    getattr(manager, action).assert_not_called()
    adapter._cosmic_handles[toplevel] = cosmic_handle
    getattr(adapter, action)(toplevel)
    expected = (
        (cosmic_handle, adapter._seat) if action == "activate" else (cosmic_handle,)
    )
    getattr(manager, action).assert_called_once_with(*expected)


def test_cosmic_overlap_dispatcher_accepts_the_proxy_argument():
    notification = _Handle()
    notification.destroy = MagicMock()
    adapter = CosmicOverlapAdapter()
    adapter._overlap_notify = SimpleNamespace(
        notify_on_overlap=MagicMock(return_value=notification)
    )
    change = MagicMock()
    adapter.start(object(), change)
    toplevel = object()
    notification.dispatcher["toplevel_enter"](notification, toplevel, 0, 0, 100, 100)
    notification.dispatcher["toplevel_leave"](notification, toplevel)
    notification.dispatcher["layer_enter"](
        notification, "panel", "panel", 1, 2, 0, 0, 100, 100
    )
    notification.dispatcher["layer_leave"](notification, "panel")
    assert [call.args[0] for call in change.call_args_list] == [
        True,
        False,
        True,
        False,
    ]
