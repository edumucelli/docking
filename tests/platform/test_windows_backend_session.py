"""Windows session composition does not instantiate reduced fallback services."""

from unittest.mock import Mock

import pytest

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

_START_ORDER = (
    "workspaces",
    "windows",
    "previews",
    "surface",
    "visibility",
    "idle",
    "screen_capture",
    "desktop_actions",
    "window_picker",
)


@pytest.mark.parametrize(
    ("failure", "role"),
    [(None, None)]
    + [(operation, role) for operation in ("start", "stop") for role in _START_ORDER],
)
def test_windows_composes_native_services_without_constructing_reduced_fallbacks(
    monkeypatch, failure, role
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
    for module, name, service_role in (
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
        services[service_role] = service
        factory = Mock(return_value=service)
        factories[service_role] = factory
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
    events = []

    def record(operation, name):
        events.append((operation, name))
        if (operation, name) == (failure, role):
            raise OSError(f"{operation} failed")

    for name, service in services.items():
        service.start.side_effect = lambda name=name: record("start", name)
        service.stop.side_effect = lambda name=name: record("stop", name)
    if failure == "start":
        with pytest.raises(OSError, match="start failed"):
            backend.start()
        expected_start = _START_ORDER[: _START_ORDER.index(role) + 1]
    else:
        backend.start()
        expected_start = _START_ORDER
    backend.stop()
    backend.stop()
    for service in services.values():
        service.stop.assert_called_once()
    assert events == [("start", name) for name in expected_start] + [
        ("stop", name) for name in reversed(_START_ORDER)
    ]
    forbidden.assert_not_called()
