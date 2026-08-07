# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Niri session backend with layer-shell surfaces and IPC window state."""

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
from docking.platform.backends.wayland.idle import WaylandIdleService
from docking.platform.backends.wayland.niri_ipc import (
    NiriDesktopActionService,
    NiriScreenCaptureService,
    NiriWindowService,
    _niri_socket_path,
    load_niri_desktop_action_service,
    load_niri_preview_service,
    load_niri_window_service,
    load_niri_workspace_service,
)
from docking.platform.backends.wayland.portals import (
    WaylandPortalColorPickerService,
    load_portal_color_picker,
)
from docking.platform.backends.wayland.runtime import WaylandProtocolRuntime
from docking.platform.backends.wayland.services import WaylandLayerShellSurfaceService
from docking.platform.backends.wayland.workspaces import WaylandWorkspaceService

if TYPE_CHECKING:
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.model import DockModel


@dataclass(frozen=True)
class NiriRuntimeServices:
    """Concrete services selected for the Niri Wayland backend."""

    windows: WindowService
    previews: PreviewService
    surface: WaylandLayerShellSurfaceService
    visibility: ReducedVisibilityService
    workspaces: WorkspaceService | None
    screen_capture: ScreenCaptureService | None
    desktop_actions: DesktopActionService | None
    idle: IdleService | None
    protocol_runtime: WaylandProtocolRuntime | None


class NiriSessionBackend(SessionBackend):
    """SessionBackend using Niri IPC for windows and layer-shell surfaces."""

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
    ) -> None:
        runtime = protocol_runtime
        if runtime is None:
            candidate_runtime = WaylandProtocolRuntime()
            if candidate_runtime.start():
                runtime = candidate_runtime

        windows = window_service or load_niri_window_service(
            model=model,
            application_registry=application_registry,
            process_identity_service=process_identity_service,
        )
        if windows is None:
            windows = ReducedWindowService()

        workspaces = load_niri_workspace_service()
        if workspaces is None:
            workspace_protocol = (
                runtime.workspace_protocol if runtime is not None else None
            )
            workspaces = (
                WaylandWorkspaceService(protocol=workspace_protocol)
                if workspace_protocol is not None
                else None
            )

        niri_previews = load_niri_preview_service()
        previews: PreviewService = niri_previews or ReducedPreviewService()
        desktop_actions = load_niri_desktop_action_service()
        idle_protocol = runtime.idle_protocol if runtime is not None else None
        idle: IdleService | None = (
            WaylandIdleService(protocol=idle_protocol)
            if idle_protocol is not None
            else None
        )

        # Prefer Niri's native PickColor IPC over the XDG desktop portal.
        if screen_capture is None:
            socket_path = _niri_socket_path()
            if socket_path is not None and socket_path.exists():
                screen_capture = NiriScreenCaptureService(socket_path=str(socket_path))
            else:
                screen_capture = load_portal_color_picker()

        self._services = NiriRuntimeServices(
            windows=windows,
            previews=previews,
            surface=WaylandLayerShellSurfaceService(layer_shell=layer_shell),
            visibility=ReducedVisibilityService(),
            workspaces=workspaces,
            screen_capture=screen_capture,
            desktop_actions=desktop_actions,
            idle=idle,
            protocol_runtime=runtime,
        )

    @property
    def name(self) -> str:
        return "niri"

    @property
    def display_server(self) -> DisplayServer:
        return DisplayServer.WAYLAND

    @property
    def capabilities(self) -> PlatformCapabilities:
        tracks_windows = isinstance(self._services.windows, NiriWindowService)
        supports_workspaces = self._services.workspaces is not None
        supports_color_pick = isinstance(
            self._services.screen_capture,
            WaylandPortalColorPickerService | NiriScreenCaptureService,
        )
        return PlatformCapabilities(
            tracks_windows=tracks_windows,
            tracks_active_window=tracks_windows,
            tracks_attention=tracks_windows,
            tracks_minimized=False,  # Niri is a tiling compositor
            tracks_fullscreen=False,
            supports_activate=tracks_windows,
            supports_minimize=False,  # Niri is a tiling compositor
            supports_close=tracks_windows,
            tracks_window_geometry=tracks_windows,
            tracks_window_workspace=tracks_windows,
            supports_current_workspace_filter=tracks_windows,
            supports_workspace_list=supports_workspaces,
            supports_workspace_switch=supports_workspaces,
            supports_show_desktop=isinstance(
                self._services.desktop_actions,
                NiriDesktopActionService,
            ),
            supports_layer_shell=True,
            supports_screen_reservation=True,
            supports_input_region=True,
            supports_screen_color_pick=supports_color_pick,
            supports_idle_time=isinstance(self._services.idle, WaylandIdleService),
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
        if self._services.workspaces is not None:
            self._services.workspaces.start()
        if self._services.desktop_actions is not None:
            self._services.desktop_actions.start()
        if self._services.screen_capture is not None:
            self._services.screen_capture.start()
        if self._services.idle is not None:
            self._services.idle.start()

    def stop(self) -> None:
        if self._services.idle is not None:
            self._services.idle.stop()
        if self._services.screen_capture is not None:
            self._services.screen_capture.stop()
        if self._services.desktop_actions is not None:
            self._services.desktop_actions.stop()
        if self._services.workspaces is not None:
            self._services.workspaces.stop()
        self._services.visibility.stop()
        self._services.surface.stop()
        self._services.windows.stop()
        self._services.previews.stop()
        if self._services.protocol_runtime is not None:
            self._services.protocol_runtime.stop()
