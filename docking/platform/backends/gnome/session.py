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
from typing import TYPE_CHECKING

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
from docking.platform.backends.dbus_idle import MutterIdleService
from docking.platform.backends.gnome.bridge import (
    GnomeShellBridgeClient,
    GnomeShellBridgeDesktopActionService,
    GnomeShellBridgePreviewService,
    GnomeShellBridgeSurfaceService,
    GnomeShellBridgeWindowService,
    GnomeShellBridgeWorkspaceService,
)
from docking.platform.backends.visibility import SnapshotVisibilityService
from docking.platform.backends.wayland.portals import load_portal_color_picker

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry


@dataclass(frozen=True)
class GnomeShellBridgeRuntimeServices:
    """Concrete services for the GNOME Shell bridge prototype."""

    windows: GnomeShellBridgeWindowService
    workspaces: GnomeShellBridgeWorkspaceService
    previews: GnomeShellBridgePreviewService
    surface: GnomeShellBridgeSurfaceService
    visibility: SnapshotVisibilityService
    desktop_actions: GnomeShellBridgeDesktopActionService
    idle: IdleService | None
    screen_capture: ScreenCaptureService | None


class GnomeShellBridgeSessionBackend(SessionBackend):
    """GNOME Shell bridge backend with reduced GTK surface integration."""

    def __init__(
        self,
        *,
        model,
        bridge: GnomeShellBridgeClient,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
        config: Config | None = None,
    ) -> None:
        windows = GnomeShellBridgeWindowService(
            model=model,
            application_registry=application_registry,
            process_identity_service=process_identity_service,
            bridge=bridge,
        )
        self._services = GnomeShellBridgeRuntimeServices(
            windows=windows,
            workspaces=GnomeShellBridgeWorkspaceService(bridge=bridge),
            previews=GnomeShellBridgePreviewService(bridge=bridge),
            surface=GnomeShellBridgeSurfaceService(bridge=bridge),
            visibility=SnapshotVisibilityService(
                windows=windows,
                visible_windows=lambda _rect: windows.list_all_windows(),
                config=config,
            ),
            desktop_actions=GnomeShellBridgeDesktopActionService(bridge=bridge),
            idle=MutterIdleService.connect(),
            screen_capture=load_portal_color_picker(),
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
            supports_overlap_active=True,
            supports_overlap_any=True,
            supports_overlap_maximized=True,
            supports_idle_time=self._services.idle is not None,
            supports_screen_color_pick=self._services.screen_capture is not None,
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
        return self._services.screen_capture

    @property
    def idle(self) -> IdleService | None:
        return self._services.idle

    @property
    def window_picker(self) -> WindowPickService | None:
        return None

    def start(self) -> None:
        self._services.previews.start()
        self._services.windows.start()
        self._services.surface.start()
        self._services.visibility.start()
        self._services.workspaces.start()
        if self._services.idle is not None:
            self._services.idle.start()
        if self._services.screen_capture is not None:
            self._services.screen_capture.start()

    def stop(self) -> None:
        if self._services.screen_capture is not None:
            self._services.screen_capture.stop()
        if self._services.idle is not None:
            self._services.idle.stop()
        self._services.workspaces.stop()
        self._services.visibility.stop()
        self._services.surface.stop()
        self._services.windows.stop()
        self._services.previews.stop()
