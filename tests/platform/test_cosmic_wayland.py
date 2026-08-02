"""Tests for COSMIC-specific Wayland protocol composition."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.platform.backends.base import Rect
from docking.platform.backends.wayland.cosmic import (
    CosmicOverlapAdapter,
    CosmicToplevelAdapter,
)
from docking.platform.backends.wayland.cosmic_session import (
    CosmicOverlapVisibilityService,
    CosmicSessionBackend,
)
from docking.platform.backends.wayland.toplevels import (
    WaylandForeignToplevelWindowService,
)


class _Handle:
    def __init__(self) -> None:
        self.dispatcher: dict[str, object] = {}


def _model() -> SimpleNamespace:
    return SimpleNamespace(
        visible_items=MagicMock(
            return_value=[SimpleNamespace(desktop_id="foot.desktop", wm_class="foot")]
        ),
        update_running=MagicMock(),
    )


def _launcher() -> SimpleNamespace:
    desktop = SimpleNamespace(desktop_id="foot.desktop")
    return SimpleNamespace(
        resolve=MagicMock(
            side_effect=lambda desktop_id, **_: (
                desktop if desktop_id == "foot.desktop" else None
            )
        ),
        resolve_by_wm_class=MagicMock(return_value=desktop),
    )


def test_cosmic_info_batch_publishes_state_geometry_and_workspace() -> None:
    model = _model()
    adapter = CosmicToplevelAdapter()
    service = WaylandForeignToplevelWindowService(
        model=model,
        launcher=_launcher(),
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
    assert snapshot.geometry == Rect(x=10, y=20, width=800, height=600)
    assert snapshot.workspace_id == "workspace-2"

    adapter._on_cosmic_workspace_leave(toplevel, workspace)
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


def test_cosmic_session_reports_only_delivered_toplevel_capabilities() -> None:
    runtime = SimpleNamespace(
        cosmic_toplevel_protocol=SimpleNamespace(),
        cosmic_overlap_protocol=None,
        preview_protocol=None,
        hyprland_preview_protocol=None,
        foreign_toplevel_protocol=None,
        workspace_protocol=None,
        stop=MagicMock(),
    )
    backend = CosmicSessionBackend(
        layer_shell=SimpleNamespace(),
        model=_model(),
        launcher=_launcher(),
        protocol_runtime=runtime,
        screen_capture=MagicMock(),
    )

    assert backend.capabilities.tracks_window_geometry is True
    assert backend.capabilities.tracks_window_workspace is True
    assert backend.workspaces is None
