"""Tests for the native Wayland layer-shell backend."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.core.position import Position
from docking.platform.backends.base import (
    DisplayServer,
    MonitorSnapshot,
    PlacementRequest,
    Rect,
    ReservationRequest,
    Size,
)
from docking.platform.backends.reduced.services import (
    ReducedPreviewService,
    ReducedVisibilityService,
    ReducedWindowService,
)
from docking.platform.backends.wayland.hyprland_ipc import (
    HyprlandSocketPaths,
    HyprlandWindowService,
)
from docking.platform.backends.wayland.hyprland_session import HyprlandSessionBackend
from docking.platform.backends.wayland.portals import WaylandPortalColorPickerService
from docking.platform.backends.wayland.previews import (
    HyprlandPreviewService,
    WaylandPreviewService,
)
from docking.platform.backends.wayland.services import (
    WaylandLayerShellSurfaceService,
    layer_shell_is_supported,
)
from docking.platform.backends.wayland.session import WaylandLayerShellSessionBackend
from docking.platform.backends.wayland.toplevels import (
    WaylandForeignToplevelWindowService,
)
from docking.platform.backends.wayland.workspaces import WaylandWorkspaceService


def _layer_shell() -> SimpleNamespace:
    return SimpleNamespace(
        Edge=SimpleNamespace(TOP="top", BOTTOM="bottom", LEFT="left", RIGHT="right"),
        Layer=SimpleNamespace(TOP="top-layer"),
        KeyboardMode=SimpleNamespace(NONE="no-keyboard"),
        init_for_window=MagicMock(),
        set_namespace=MagicMock(),
        set_layer=MagicMock(),
        set_keyboard_mode=MagicMock(),
        set_anchor=MagicMock(),
        set_margin=MagicMock(),
        set_monitor=MagicMock(),
        set_size=MagicMock(),
        set_exclusive_zone=MagicMock(),
    )


def _monitor_snapshot() -> MonitorSnapshot:
    return MonitorSnapshot(
        index=1,
        geometry=Rect(x=100, y=200, width=800, height=600),
        workarea=Rect(x=100, y=220, width=800, height=560),
        scale=1,
        primary=False,
    )


def _empty_runtime() -> SimpleNamespace:
    return SimpleNamespace(
        foreign_toplevel_protocol=None,
        workspace_protocol=None,
        preview_protocol=None,
        hyprland_preview_protocol=None,
        stop=MagicMock(),
    )


def test_wayland_layer_shell_session_exposes_surface_capabilities():
    screen_capture = WaylandPortalColorPickerService(picker=lambda: (0, 0, 0))
    backend = WaylandLayerShellSessionBackend(
        layer_shell=_layer_shell(),
        protocol_runtime=_empty_runtime(),
        screen_capture=screen_capture,
    )

    assert backend.name == "wayland-layer-shell"
    assert backend.display_server is DisplayServer.WAYLAND
    assert backend.capabilities.supports_layer_shell is True
    assert backend.capabilities.supports_screen_reservation is True
    assert backend.capabilities.supports_input_region is True
    assert backend.capabilities.tracks_windows is False
    assert isinstance(backend.windows, ReducedWindowService)
    assert isinstance(backend.previews, ReducedPreviewService)
    assert isinstance(backend.visibility, ReducedVisibilityService)
    assert isinstance(backend.surface, WaylandLayerShellSurfaceService)
    assert backend.workspaces is None
    assert backend.desktop_actions is None
    assert backend.screen_capture is screen_capture
    assert backend.idle is None
    assert backend.window_picker is None


def test_wayland_layer_shell_session_uses_foreign_toplevel_service_when_available():
    backend = WaylandLayerShellSessionBackend(
        layer_shell=_layer_shell(),
        model=SimpleNamespace(
            visible_items=MagicMock(return_value=[]),
            update_running=MagicMock(),
        ),
        launcher=SimpleNamespace(resolve=MagicMock(), resolve_by_wm_class=MagicMock()),
        foreign_toplevel_protocol=SimpleNamespace(),
        protocol_runtime=_empty_runtime(),
    )

    assert isinstance(backend.windows, WaylandForeignToplevelWindowService)
    assert backend.capabilities.tracks_windows is True
    assert backend.capabilities.tracks_active_window is True
    assert backend.capabilities.supports_window_menu is True
    assert backend.capabilities.supports_activate is True
    assert backend.capabilities.supports_minimize is True
    assert backend.capabilities.supports_close is True
    assert backend.capabilities.tracks_window_geometry is False
    assert backend.capabilities.tracks_window_workspace is False


def test_wayland_layer_shell_session_uses_wayland_previews_when_available():
    preview_protocol = SimpleNamespace(
        capture_available=True,
        start=MagicMock(),
        stop=MagicMock(),
    )
    backend = WaylandLayerShellSessionBackend(
        layer_shell=_layer_shell(),
        model=SimpleNamespace(
            visible_items=MagicMock(return_value=[]),
            update_running=MagicMock(),
        ),
        launcher=SimpleNamespace(resolve=MagicMock(), resolve_by_wm_class=MagicMock()),
        foreign_toplevel_protocol=SimpleNamespace(),
        protocol_runtime=SimpleNamespace(
            foreign_toplevel_protocol=None,
            workspace_protocol=None,
            preview_protocol=preview_protocol,
            hyprland_preview_protocol=None,
            stop=MagicMock(),
        ),
    )

    assert isinstance(backend.windows, WaylandForeignToplevelWindowService)
    assert isinstance(backend.previews, WaylandPreviewService)


def test_wayland_layer_shell_session_uses_hyprland_previews_when_available():
    hyprland_preview_protocol = SimpleNamespace(
        capture_available=True,
        create_frame=MagicMock(),
        create_shm_pool=MagicMock(),
        flush=MagicMock(),
    )
    backend = WaylandLayerShellSessionBackend(
        layer_shell=_layer_shell(),
        model=SimpleNamespace(
            visible_items=MagicMock(return_value=[]),
            update_running=MagicMock(),
        ),
        launcher=SimpleNamespace(resolve=MagicMock(), resolve_by_wm_class=MagicMock()),
        foreign_toplevel_protocol=SimpleNamespace(),
        protocol_runtime=SimpleNamespace(
            foreign_toplevel_protocol=None,
            workspace_protocol=None,
            preview_protocol=None,
            hyprland_preview_protocol=hyprland_preview_protocol,
            stop=MagicMock(),
        ),
    )

    assert isinstance(backend.windows, WaylandForeignToplevelWindowService)
    assert isinstance(backend.previews, HyprlandPreviewService)


def test_wayland_layer_shell_session_uses_workspace_and_capture_services_when_available():
    screen_capture = WaylandPortalColorPickerService(picker=lambda: (0, 0, 0))
    backend = WaylandLayerShellSessionBackend(
        layer_shell=_layer_shell(),
        workspace_protocol=SimpleNamespace(),
        screen_capture=screen_capture,
        protocol_runtime=_empty_runtime(),
    )

    assert isinstance(backend.workspaces, WaylandWorkspaceService)
    assert backend.screen_capture is screen_capture
    assert backend.idle is None
    assert backend.capabilities.supports_workspace_list is True
    assert backend.capabilities.supports_workspace_switch is True
    assert backend.capabilities.supports_screen_color_pick is True


def test_hyprland_session_uses_ipc_windows_and_layer_shell_capabilities():
    window_service = HyprlandWindowService(
        model=SimpleNamespace(
            visible_items=MagicMock(return_value=[]),
            update_running=MagicMock(),
        ),
        launcher=SimpleNamespace(resolve=MagicMock(), resolve_by_wm_class=MagicMock()),
        client=SimpleNamespace(paths=HyprlandSocketPaths(command="", events="")),
    )
    backend = HyprlandSessionBackend(
        layer_shell=_layer_shell(),
        model=SimpleNamespace(),
        launcher=SimpleNamespace(),
        protocol_runtime=_empty_runtime(),
        window_service=window_service,
    )

    assert backend.name == "hyprland"
    assert backend.display_server is DisplayServer.WAYLAND
    assert backend.windows is window_service
    assert backend.capabilities.tracks_windows is True
    assert backend.capabilities.tracks_active_window is True
    assert backend.capabilities.tracks_attention is True
    assert backend.capabilities.tracks_window_geometry is True
    assert backend.capabilities.tracks_window_workspace is True
    assert backend.capabilities.supports_current_workspace_filter is True
    assert backend.capabilities.supports_activate is True
    assert backend.capabilities.supports_minimize is True
    assert backend.capabilities.supports_close is True
    assert backend.capabilities.supports_layer_shell is True
    assert backend.capabilities.supports_screen_reservation is True
    assert backend.capabilities.supports_input_region is True


def test_hyprland_session_falls_back_to_reduced_windows_when_ipc_unavailable(
    monkeypatch,
):
    monkeypatch.setattr(
        "docking.platform.backends.wayland.hyprland_session."
        "load_hyprland_window_service",
        lambda **_: None,
    )
    backend = HyprlandSessionBackend(
        layer_shell=_layer_shell(),
        model=SimpleNamespace(),
        launcher=SimpleNamespace(),
        protocol_runtime=_empty_runtime(),
    )

    assert isinstance(backend.windows, ReducedWindowService)
    assert backend.capabilities.tracks_windows is False
    assert backend.capabilities.supports_layer_shell is True


def test_wayland_layer_shell_session_lifecycle_is_safe():
    backend = WaylandLayerShellSessionBackend(
        layer_shell=_layer_shell(),
        protocol_runtime=_empty_runtime(),
    )

    backend.start()
    backend.start()
    backend.stop()
    backend.stop()


def test_configure_before_realize_assigns_layer_shell_role():
    layer_shell = _layer_shell()
    window = MagicMock()
    service = WaylandLayerShellSurfaceService(layer_shell=layer_shell)

    service.configure_before_realize(window)

    window.set_accept_focus.assert_called_once_with(False)
    window.set_focus_on_map.assert_called_once_with(False)
    layer_shell.init_for_window.assert_called_once_with(window)
    layer_shell.set_namespace.assert_called_once_with(window, "docking")
    layer_shell.set_layer.assert_called_once_with(window, "top-layer")
    layer_shell.set_keyboard_mode.assert_called_once_with(window, "no-keyboard")
    layer_shell.set_anchor.assert_any_call(window, "bottom", True)
    layer_shell.set_anchor.assert_any_call(window, "left", True)
    layer_shell.set_anchor.assert_any_call(window, "right", True)


def test_position_or_anchor_maps_placement_to_layer_shell():
    layer_shell = _layer_shell()
    monitor = object()
    display = SimpleNamespace(get_monitor=MagicMock(return_value=monitor))
    window = MagicMock()
    window.get_display.return_value = display
    service = WaylandLayerShellSurfaceService(layer_shell=layer_shell)
    service.configure_before_realize(window)

    service.position_or_anchor(
        PlacementRequest(
            monitor=_monitor_snapshot(),
            position=Position.LEFT,
            x=100,
            y=220,
            size=Size(width=64, height=560),
            gap=8,
        )
    )

    display.get_monitor.assert_called_once_with(1)
    layer_shell.set_monitor.assert_called_once_with(window, monitor)
    layer_shell.set_anchor.assert_any_call(window, "left", True)
    layer_shell.set_anchor.assert_any_call(window, "top", True)
    layer_shell.set_anchor.assert_any_call(window, "bottom", True)
    layer_shell.set_anchor.assert_any_call(window, "right", False)
    layer_shell.set_size.assert_called_once_with(window, 64, 560)
    window.set_size_request.assert_called_with(64, 560)
    window.resize.assert_called_with(64, 560)
    window.move.assert_not_called()
    assert service.popups_use_parent_relative_coordinates is True
    assert service.get_surface_position() == (100, 220)


def test_layer_shell_surface_position_clears_on_stop():
    service = WaylandLayerShellSurfaceService(layer_shell=_layer_shell())
    service.configure_before_realize(MagicMock())

    service.position_or_anchor(
        PlacementRequest(
            monitor=_monitor_snapshot(),
            position=Position.BOTTOM,
            x=100,
            y=720,
            size=Size(width=800, height=64),
        )
    )
    service.stop()

    assert service.get_surface_position() is None


def test_reservation_updates_layer_shell_exclusive_zone():
    layer_shell = _layer_shell()
    window = MagicMock()
    service = WaylandLayerShellSurfaceService(layer_shell=layer_shell)
    service.configure_before_realize(window)

    service.set_reservation(
        ReservationRequest(
            monitor=_monitor_snapshot(),
            position=Position.BOTTOM,
            thickness=48,
        )
    )
    service.clear_reservation()

    assert layer_shell.set_exclusive_zone.call_args_list[-2].args == (window, 48)
    assert layer_shell.set_exclusive_zone.call_args_list[-1].args == (window, 0)


def test_update_input_region_uses_gtk_input_shape():
    layer_shell = _layer_shell()
    window = MagicMock()
    service = WaylandLayerShellSurfaceService(layer_shell=layer_shell)
    service.configure_before_realize(window)

    service.update_input_region(Rect(x=1, y=2, width=30, height=40))

    region = window.input_shape_combine_region.call_args.args[0]
    extents = region.get_extents()
    assert (extents.x, extents.y, extents.width, extents.height) == (1, 2, 30, 40)


def test_layer_shell_support_probe_handles_missing_or_failing_probe():
    assert layer_shell_is_supported(SimpleNamespace()) is True
    assert layer_shell_is_supported(SimpleNamespace(is_supported=lambda: True)) is True

    def broken_probe():
        raise RuntimeError("boom")

    assert layer_shell_is_supported(SimpleNamespace(is_supported=broken_probe)) is False
