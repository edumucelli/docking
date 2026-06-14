"""Tests for the reduced session backend."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call

from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    MonitorSnapshot,
    PlacementRequest,
    Rect,
    ReservationRequest,
    Size,
    WindowId,
)
from docking.platform.backends.reduced.services import (
    ReducedPreviewService,
    ReducedSurfaceService,
    ReducedVisibilityService,
    ReducedWindowService,
)
from docking.platform.backends.reduced.session import ReducedSessionBackend


def test_reduced_session_exposes_reduced_capabilities_and_services():
    backend = ReducedSessionBackend()

    assert backend.name == "reduced"
    assert backend.display_server is DisplayServer.NONE
    assert backend.capabilities == backend.capabilities.__class__()
    assert isinstance(backend.windows, ReducedWindowService)
    assert isinstance(backend.previews, ReducedPreviewService)
    assert isinstance(backend.surface, ReducedSurfaceService)
    assert isinstance(backend.visibility, ReducedVisibilityService)
    assert backend.workspaces is None
    assert backend.desktop_actions is None
    assert backend.screen_capture is None
    assert backend.idle is None
    assert backend.window_picker is None


def test_reduced_session_lifecycle_is_safe():
    backend = ReducedSessionBackend()

    backend.start()
    backend.start()
    backend.stop()
    backend.stop()


def test_reduced_window_service_has_no_windows_and_unsupported_actions():
    service = ReducedWindowService()
    window_id = WindowId.x11(1)

    assert service.list_windows("firefox.desktop") == ()
    assert service.list_preview_windows("firefox.desktop") == ()
    assert (
        service.icon_name_for_desktop("firefox.desktop") == "application-x-executable"
    )
    assert service.activate(window_id) is ActionResult.UNSUPPORTED
    assert service.activate_most_recent("firefox.desktop") is ActionResult.UNSUPPORTED
    assert service.cycle("firefox.desktop") is ActionResult.UNSUPPORTED
    assert service.minimize_all("firefox.desktop") is ActionResult.UNSUPPORTED
    assert service.close(window_id) is ActionResult.UNSUPPORTED
    assert service.close_all("firefox.desktop") is ActionResult.UNSUPPORTED
    assert service.close_focused("firefox.desktop") is ActionResult.UNSUPPORTED
    assert service.toggle_focus("firefox.desktop") is ActionResult.UNSUPPORTED


def test_reduced_preview_and_visibility_services_are_unavailable():
    preview = ReducedPreviewService()
    visibility = ReducedVisibilityService()

    assert preview.capture(WindowId.x11(1), width=200, height=150) is None
    assert preview.thumbnail(WindowId.x11(1), width=28, height=20) is None
    assert (
        visibility.create_monitor(
            get_dock_rect=lambda: Rect(x=0, y=0, width=1, height=1),
            on_change=lambda _value: None,
        )
        is None
    )


def test_reduced_surface_service_applies_generic_move_and_resize():
    window = MagicMock()
    service = ReducedSurfaceService()

    service.position_or_anchor(
        PlacementRequest(
            monitor=MonitorSnapshot(
                index=0,
                geometry=Rect(x=0, y=0, width=1920, height=1080),
            ),
            position=SimpleNamespace(value="bottom"),
            x=10,
            y=20,
            size=Size(width=300, height=40),
        )
    )
    window.set_size_request.assert_not_called()

    service.configure_before_realize(window)
    service.on_realize(window)

    assert window.set_skip_taskbar_hint.call_args_list == [call(True), call(True)]
    assert window.set_skip_pager_hint.call_args_list == [call(True), call(True)]
    assert window.set_accept_focus.call_args_list == [call(False), call(False)]
    assert window.set_focus_on_map.call_args_list == [call(False), call(False)]
    assert window.stick.call_count == 2
    assert window.set_keep_above.call_args_list == [call(True), call(True)]

    service.position_or_anchor(
        PlacementRequest(
            monitor=MonitorSnapshot(
                index=0,
                geometry=Rect(x=0, y=0, width=1920, height=1080),
            ),
            position=SimpleNamespace(value="bottom"),
            x=10,
            y=20,
            size=Size(width=300, height=40),
        )
    )

    window.set_size_request.assert_called_once_with(300, 40)
    window.resize.assert_called_once_with(300, 40)
    window.move.assert_called_once_with(10, 20)
    service.set_workspace_scope(current_workspace_only=True)
    service.set_reservation(
        ReservationRequest(
            monitor=MonitorSnapshot(
                index=0,
                geometry=Rect(x=0, y=0, width=1920, height=1080),
            ),
            position=SimpleNamespace(value="bottom"),
            thickness=40,
        )
    )
    service.clear_reservation()
    service.update_pointer_barrier(
        monitor=None,
        position=SimpleNamespace(value="bottom"),
        enabled=False,
    )
    service.update_input_region(Rect(x=1, y=2, width=3, height=4))
    service.set_blur_region(None)
