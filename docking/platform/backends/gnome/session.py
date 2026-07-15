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

"""GNOME Shell bridge session backend."""

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
from docking.platform.backends.gnome.bridge import (
    GnomeShellBridgeClient,
    GnomeShellBridgeDesktopActionService,
    GnomeShellBridgePreviewService,
    GnomeShellBridgeSurfaceService,
    GnomeShellBridgeWindowService,
    GnomeShellBridgeWorkspaceService,
)
from docking.platform.backends.reduced.services import (
    ReducedVisibilityService,
)


@dataclass(frozen=True)
class GnomeShellBridgeRuntimeServices:
    """Concrete services for the GNOME Shell bridge prototype."""

    windows: GnomeShellBridgeWindowService
    workspaces: GnomeShellBridgeWorkspaceService
    previews: GnomeShellBridgePreviewService
    surface: GnomeShellBridgeSurfaceService
    visibility: ReducedVisibilityService
    desktop_actions: GnomeShellBridgeDesktopActionService


class GnomeShellBridgeSessionBackend(SessionBackend):
    """GNOME Shell bridge backend with reduced GTK surface integration."""

    def __init__(self, *, model, launcher, bridge: GnomeShellBridgeClient) -> None:
        self._services = GnomeShellBridgeRuntimeServices(
            windows=GnomeShellBridgeWindowService(
                model=model,
                launcher=launcher,
                bridge=bridge,
            ),
            workspaces=GnomeShellBridgeWorkspaceService(bridge=bridge),
            previews=GnomeShellBridgePreviewService(bridge=bridge),
            surface=GnomeShellBridgeSurfaceService(bridge=bridge),
            visibility=ReducedVisibilityService(),
            desktop_actions=GnomeShellBridgeDesktopActionService(bridge=bridge),
        )

    @property
    def name(self) -> str:
        return "gnome-shell-bridge"

    @property
    def display_server(self) -> DisplayServer:
        return DisplayServer.WAYLAND

    @property
    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            tracks_windows=True,
            tracks_active_window=True,
            tracks_minimized=True,
            tracks_maximized=True,
            tracks_fullscreen=True,
            supports_activate=True,
            supports_minimize=True,
            supports_close=True,
            supports_window_menu=True,
            tracks_window_geometry=True,
            tracks_window_workspace=True,
            supports_current_workspace_filter=True,
            supports_workspace_list=True,
            supports_workspace_switch=True,
            supports_show_desktop=True,
        )

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
        return None

    @property
    def idle(self) -> IdleService | None:
        return None

    @property
    def window_picker(self) -> WindowPickService | None:
        return None

    def start(self) -> None:
        self._services.previews.start()
        self._services.windows.start()
        self._services.surface.start()
        self._services.visibility.start()
        self._services.workspaces.start()

    def stop(self) -> None:
        self._services.workspaces.stop()
        self._services.visibility.stop()
        self._services.surface.stop()
        self._services.windows.stop()
        self._services.previews.stop()
