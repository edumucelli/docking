"""Windows session composition does not instantiate reduced fallback services."""

from unittest.mock import Mock

from docking.platform.applications.implementations.windows.drag_source import (
    WindowsFileDragSource,
)
from docking.platform.backends.base import DisplayServer, SessionBackend
from docking.platform.backends.reduced import session as reduced
from docking.platform.backends.windows import (
    desktop,
    idle,
    picking,
    previews,
    screen_capture,
    surface,
    visibility,
    windows,
    workspaces,
)
from docking.platform.backends.windows.session import WindowsSessionBackend


def test_windows_composes_native_services_without_constructing_reduced_fallbacks(
    monkeypatch,
):
    forbidden = Mock(
        side_effect=AssertionError("Windows must own its session composition")
    )
    for name in (
        "ReducedWindowService",
        "ReducedPreviewService",
        "ReducedSurfaceService",
        "ReducedVisibilityService",
    ):
        monkeypatch.setattr(reduced, name, forbidden)
    services = {}
    factories = {}
    for module, name, role in (
        (windows, "WindowsWindowService", "windows"),
        (previews, "WindowsPreviewService", "previews"),
        (surface, "WindowsSurfaceService", "surface"),
        (visibility, "WindowsVisibilityService", "visibility"),
        (workspaces, "WindowsWorkspaceService", "workspaces"),
        (idle, "WindowsIdleService", "idle"),
        (screen_capture, "WindowsScreenCaptureService", "screen_capture"),
        (desktop, "WindowsDesktopActionService", "desktop_actions"),
        (picking, "WindowsWindowPickService", "window_picker"),
    ):
        service = Mock()
        services[role] = service
        factory = Mock(return_value=service)
        factories[role] = factory
        monkeypatch.setattr(module, name, factory)
    model, config, registry, identity = Mock(), Mock(), Mock(), Mock()
    backend = WindowsSessionBackend(
        model=model,
        config=config,
        application_registry=registry,
        process_identity_service=identity,
    )
    assert isinstance(backend, SessionBackend)
    assert backend.name == "windows" and backend.display_server is DisplayServer.WINDOWS
    assert backend.windows is services["windows"]
    assert backend.previews is services["previews"]
    assert backend.surface is services["surface"]
    assert backend.visibility is services["visibility"]
    factories["windows"].assert_called_once_with(
        model=model,
        config=config,
        application_registry=registry,
        process_identity_service=identity,
        workspaces=services["workspaces"],
    )
    factories["surface"].assert_called_once_with(
        workspaces=services["workspaces"],
        file_drag_source_factory=WindowsFileDragSource,
    )
    factories["previews"].assert_called_once_with(
        windows=services["windows"],
        availability_changed=services["windows"].set_previews_available,
    )
    factories["visibility"].assert_called_once_with(
        windows=services["windows"],
        surface=services["surface"],
        config=config,
    )
    backend.start()
    backend.stop()
    for service in services.values():
        service.start.assert_called_once()
        service.stop.assert_called_once()
    forbidden.assert_not_called()
