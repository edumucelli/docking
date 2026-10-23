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

"""Hyprland session backend with layer-shell surfaces and IPC window state."""

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
from docking.platform.backends.reduced.services import (
    ReducedPreviewService,
    ReducedVisibilityService,
    ReducedWindowService,
)
from docking.platform.backends.visibility import SnapshotVisibilityService
from docking.platform.backends.wayland.hyprland_ipc import (
    HyprlandWindowService,
    HyprlandWorkspaceService,
    load_hyprland_window_service,
)
from docking.platform.backends.wayland.idle import WaylandIdleService
from docking.platform.backends.wayland.portals import (
    WaylandPortalColorPickerService,
    load_portal_color_picker,
)
from docking.platform.backends.wayland.previews import (
    HyprlandPreviewService,
    WaylandPreviewHandleTracker,
)
from docking.platform.backends.wayland.runtime import WaylandProtocolRuntime
from docking.platform.backends.wayland.services import WaylandLayerShellSurfaceService
from docking.platform.backends.wayland.workspaces import WaylandWorkspaceService

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.model import DockModel


@dataclass(frozen=True)
class HyprlandRuntimeServices:
    """Concrete services selected for the Hyprland Wayland backend."""

    windows: WindowService
    previews: PreviewService
    surface: WaylandLayerShellSurfaceService
    visibility: VisibilityService
    workspaces: WorkspaceService | None
    screen_capture: ScreenCaptureService | None
    protocol_runtime: WaylandProtocolRuntime | None
    idle: IdleService | None


class HyprlandSessionBackend(SessionBackend):
    """SessionBackend using Hyprland IPC for windows and layer-shell surfaces."""

    def __init__(
        self,
        *,
        layer_shell: object,
        model: DockModel,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
        protocol_runtime: WaylandProtocolRuntime | None = None,
        screen_capture: ScreenCaptureService | None = None,
        window_service: WindowService | None = None,
        config: Config | None = None,
    ) -> None:
        runtime = protocol_runtime
        if runtime is None:
            candidate_runtime = WaylandProtocolRuntime()
            if candidate_runtime.start():
                runtime = candidate_runtime

        foreign_toplevel = (
            runtime.foreign_toplevel_protocol if runtime is not None else None
        )
        preview_handles = (
            WaylandPreviewHandleTracker(
                model=model,
                application_registry=application_registry,
                process_identity_service=process_identity_service,
                protocol=foreign_toplevel,
            )
            if foreign_toplevel is not None and window_service is None
            else None
        )
        windows = window_service or load_hyprland_window_service(
            model=model,
            application_registry=application_registry,
            process_identity_service=process_identity_service,
            preview_handle_source=preview_handles,
        )
        if windows is None:
            windows = ReducedWindowService()

        hyprland_preview_protocol = (
            runtime.hyprland_preview_protocol if runtime is not None else None
        )
        if (
            hyprland_preview_protocol is not None
            and isinstance(windows, HyprlandWindowService)
            and preview_handles is not None
        ):
            previews: PreviewService = HyprlandPreviewService(
                protocol=hyprland_preview_protocol,
                windows=windows,
            )
        else:
            previews = ReducedPreviewService()

        workspace_protocol = runtime.workspace_protocol if runtime is not None else None
        workspaces = (
            HyprlandWorkspaceService(windows=windows)
            if isinstance(windows, HyprlandWindowService)
            else WaylandWorkspaceService(protocol=workspace_protocol)
            if workspace_protocol is not None
            else None
        )

        self._services = HyprlandRuntimeServices(
            windows=windows,
            previews=previews,
            surface=WaylandLayerShellSurfaceService(layer_shell=layer_shell),
            visibility=SnapshotVisibilityService(
                windows=windows,
                visible_windows=lambda _rect: windows.list_all_windows(),
                config=config,
                poll_ms=250,
            )
            if isinstance(windows, HyprlandWindowService)
            else ReducedVisibilityService(),
            workspaces=workspaces,
            screen_capture=screen_capture
            if screen_capture is not None
            else load_portal_color_picker(),
            protocol_runtime=runtime,
            idle=WaylandIdleService(protocol=runtime.idle_protocol)
            if runtime is not None and runtime.idle_protocol is not None
            else None,
        )

    @property
    def name(self) -> str:
        return "hyprland"

    @property
    def display_server(self) -> DisplayServer:
        return DisplayServer.WAYLAND

    @property
    def capabilities(self) -> PlatformCapabilities:
        tracks_windows = isinstance(self._services.windows, HyprlandWindowService)
        supports_workspaces = self._services.workspaces is not None
        supports_color_pick = isinstance(
            self._services.screen_capture,
            WaylandPortalColorPickerService,
        )
        return PlatformCapabilities(
            tracks_windows=tracks_windows,
            tracks_active_window=tracks_windows,
            tracks_attention=tracks_windows,
            tracks_minimized=False,
            tracks_maximized=tracks_windows,
            tracks_fullscreen=tracks_windows,
            supports_activate=tracks_windows,
            supports_minimize=False,
            supports_close=tracks_windows,
            tracks_window_geometry=tracks_windows,
            tracks_window_workspace=tracks_windows,
            supports_current_workspace_filter=tracks_windows,
            supports_workspace_list=supports_workspaces,
            supports_workspace_switch=supports_workspaces,
            supports_layer_shell=True,
            supports_screen_reservation=True,
            supports_input_region=True,
            supports_screen_color_pick=supports_color_pick,
            supports_overlap_active=tracks_windows,
            supports_overlap_any=tracks_windows,
            supports_overlap_maximized=tracks_windows,
            supports_idle_time=self._services.idle is not None,
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
        return None

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
        if self._services.workspaces is not None:
            self._services.workspaces.start()
        if self._services.screen_capture is not None:
            self._services.screen_capture.start()
        if self._services.idle is not None:
            self._services.idle.start()

    def stop(self) -> None:
        if self._services.idle is not None:
            self._services.idle.stop()
        if self._services.screen_capture is not None:
            self._services.screen_capture.stop()
        if self._services.workspaces is not None:
            self._services.workspaces.stop()
        self._services.visibility.stop()
        self._services.surface.stop()
        self._services.windows.stop()
        self._services.previews.stop()
        if self._services.protocol_runtime is not None:
            self._services.protocol_runtime.stop()
