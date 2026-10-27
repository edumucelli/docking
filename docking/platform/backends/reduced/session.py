# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Reduced session backend with no taskbar/window-manager powers."""

from __future__ import annotations

from dataclasses import dataclass

from docking.platform.backends.base import (
    DesktopActionService,
    DisplayServer,
    IdleService,
    PlatformCapabilities,
    PreviewService,
    ScreenCaptureService,
    SessionBackend,
    SurfaceService,
    VisibilityService,
    WindowPickService,
    WindowService,
    WorkspaceService,
)
from docking.platform.backends.reduced.services import (
    ReducedPreviewService,
    ReducedSurfaceService,
    ReducedVisibilityService,
    ReducedWindowService,
)


@dataclass(frozen=True)
class ReducedRuntimeServices:
    """Concrete mandatory services for reduced mode."""

    windows: ReducedWindowService
    previews: ReducedPreviewService
    surface: ReducedSurfaceService
    visibility: ReducedVisibilityService


class ReducedSessionBackend(SessionBackend):
    """Backend used to validate Docking without X11 taskbar integration."""

    def __init__(self) -> None:
        self._services = ReducedRuntimeServices(
            windows=ReducedWindowService(),
            previews=ReducedPreviewService(),
            surface=ReducedSurfaceService(),
            visibility=ReducedVisibilityService(),
        )

    @property
    def name(self) -> str:
        return "reduced"

    @property
    def display_server(self) -> DisplayServer:
        return DisplayServer.NONE

    @property
    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities()

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
        return None

    @property
    def desktop_actions(self) -> DesktopActionService | None:
        return None

    @property
    def screen_capture(self) -> ScreenCaptureService | None:
        return None

    @property
    def idle(self) -> IdleService | None:
        return None

    @property
    def window_picker(self) -> WindowPickService | None:
        return None

    def start(self) -> None:
        self._services.windows.start()
        self._services.previews.start()
        self._services.surface.start()
        self._services.visibility.start()

    def stop(self) -> None:
        self._services.visibility.stop()
        self._services.surface.stop()
        self._services.previews.stop()
        self._services.windows.stop()
