"""Shared service composition and lifecycle for native Wayland sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from docking.platform.backends.base import (
    DesktopActionService,
    DisplayServer,
    IdleService,
    PreviewService,
    ScreenCaptureService,
    Service,
    SessionBackend,
    SurfaceService,
    VisibilityService,
    WindowPickService,
    WindowService,
    WorkspaceService,
)

if TYPE_CHECKING:
    from docking.platform.backends.wayland.runtime import WaylandProtocolRuntime


@dataclass(frozen=True)
class WaylandSessionServices:
    """Services owned by one native Wayland session backend."""

    windows: WindowService
    previews: PreviewService
    surface: SurfaceService
    visibility: VisibilityService
    workspaces: WorkspaceService | None = None
    desktop_actions: DesktopActionService | None = None
    screen_capture: ScreenCaptureService | None = None
    idle: IdleService | None = None
    window_picker: WindowPickService | None = None
    protocol_runtime: WaylandProtocolRuntime | None = None

    def lifecycle_order(self) -> tuple[Service | None, ...]:
        """Return services in dependency-safe startup order."""
        return (
            self.previews,
            self.windows,
            self.surface,
            self.visibility,
            self.workspaces,
            self.desktop_actions,
            self.screen_capture,
            self.idle,
            self.window_picker,
        )


class ComposedWaylandSessionBackend(SessionBackend):
    """Provide common service access and lifecycle for Wayland backends."""

    def __init__(self, *, services: WaylandSessionServices) -> None:
        self._services = services

    @property
    def display_server(self) -> DisplayServer:
        return DisplayServer.WAYLAND

    @property
    def windows(self) -> WindowService:
        return self._services.windows

    @property
    def surface(self) -> SurfaceService:
        return self._services.surface

    @property
    def visibility(self) -> VisibilityService:
        return self._services.visibility

    @property
    def previews(self) -> PreviewService:
        return self._services.previews

    @property
    def workspaces(self) -> WorkspaceService | None:
        return self._services.workspaces

    @property
    def desktop_actions(self) -> DesktopActionService | None:
        return self._services.desktop_actions

    @property
    def screen_capture(self) -> ScreenCaptureService | None:
        return self._services.screen_capture

    @property
    def idle(self) -> IdleService | None:
        return self._services.idle

    @property
    def window_picker(self) -> WindowPickService | None:
        return self._services.window_picker

    def start(self) -> None:
        for service in self._services.lifecycle_order():
            if service is not None:
                service.start()

    def stop(self) -> None:
        for service in reversed(self._services.lifecycle_order()):
            if service is not None:
                service.stop()
        if self._services.protocol_runtime is not None:
            self._services.protocol_runtime.stop()
