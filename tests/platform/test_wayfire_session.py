"""Tests for the Wayfire session backend capability surface."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.platform.backends.wayland import wayfire_session
from docking.platform.backends.wayland.wayfire_ipc import (
    WayfireWindowPickService,
    WayfireWindowService,
    WayfireWorkspaceService,
)


def _service() -> SimpleNamespace:
    return SimpleNamespace(start=MagicMock(), stop=MagicMock())


def test_wayfire_session_capabilities_reflect_supported_ipc_surface(monkeypatch):
    surface = _service()
    monkeypatch.setattr(
        wayfire_session,
        "WaylandLayerShellSurfaceService",
        MagicMock(return_value=surface),
    )
    monkeypatch.setattr(wayfire_session, "load_portal_color_picker", lambda: None)
    monkeypatch.setattr(
        wayfire_session, "load_wayfire_desktop_action_service", lambda: None
    )
    monkeypatch.setattr(
        wayfire_session, "load_wayfire_visibility_service", lambda config: None
    )
    monkeypatch.setattr(wayfire_session, "load_wayfire_preview_service", lambda: None)

    windows = MagicMock(spec=WayfireWindowService)
    workspaces = MagicMock(spec=WayfireWorkspaceService)
    picker = MagicMock(spec=WayfireWindowPickService)
    backend = wayfire_session.WayfireSessionBackend(
        layer_shell=object(),
        model=MagicMock(),
        launcher=MagicMock(),
        config=MagicMock(),
        protocol_runtime=SimpleNamespace(idle_protocol=None, stop=MagicMock()),
        screen_capture=None,
        window_service=windows,
        workspace_service=workspaces,
        window_picker=picker,
        desktop_action_service=None,
        visibility_service=None,
        preview_service=None,
    )

    capabilities = backend.capabilities

    assert capabilities.tracks_windows is True
    assert capabilities.tracks_active_window is True
    assert capabilities.tracks_attention is False
    assert capabilities.tracks_minimized is True
    assert capabilities.tracks_maximized is False
    assert capabilities.tracks_fullscreen is True
    assert capabilities.tracks_stacking_order is True
    assert capabilities.supports_activate is True
    assert capabilities.supports_minimize is True
    assert capabilities.supports_close is True
    assert capabilities.supports_window_menu is True
    assert capabilities.tracks_window_geometry is True
    assert capabilities.tracks_window_workspace is True
    assert capabilities.supports_current_workspace_filter is False
    assert capabilities.supports_workspace_list is True
    assert capabilities.supports_workspace_switch is True
    assert capabilities.supports_show_desktop is False
    assert capabilities.supports_layer_shell is True
    assert capabilities.supports_screen_reservation is True
    assert capabilities.supports_input_region is True
    assert capabilities.supports_pointer_barrier is False
    assert capabilities.supports_background_blur_hint is False
    assert capabilities.supports_any_overlap is False
    assert capabilities.supports_screen_color_pick is False
    assert capabilities.supports_idle_time is False
    assert capabilities.supports_window_pick is True
    assert capabilities.supports_window_pid is True
    assert capabilities.supports_process_kill is True
