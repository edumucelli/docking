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

"""Native Wayland session backend for layer-shell and optional toplevel support."""

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
    ReducedVisibilityService,
    ReducedWindowService,
)
from docking.platform.backends.wayland.hyprland_ipc import (
    HyprlandWindowService,
    load_hyprland_window_service,
)
from docking.platform.backends.wayland.portals import (
    WaylandPortalColorPickerService,
    load_portal_color_picker,
)
from docking.platform.backends.wayland.previews import (
    HyprlandPreviewService,
    WaylandPreviewHandleTracker,
    WaylandPreviewService,
)
from docking.platform.backends.wayland.runtime import WaylandProtocolRuntime
from docking.platform.backends.wayland.services import WaylandLayerShellSurfaceService
from docking.platform.backends.wayland.toplevels import (
    WaylandForeignToplevelWindowService,
    load_foreign_toplevel_protocol,
)
from docking.platform.backends.wayland.workspaces import (
    WaylandWorkspaceService,
    load_workspace_protocol,
)


@dataclass(frozen=True)
class WaylandLayerShellRuntimeServices:
    """Concrete services selected for native Wayland support."""

    windows: WindowService
    previews: PreviewService
    surface: WaylandLayerShellSurfaceService
    visibility: ReducedVisibilityService
    workspaces: WorkspaceService | None
    screen_capture: ScreenCaptureService | None
    protocol_runtime: WaylandProtocolRuntime | None


class WaylandLayerShellSessionBackend(SessionBackend):
    """SessionBackend with native Wayland surface placement and optional windows."""

    def __init__(
        self,
        *,
        layer_shell: object,
        model=None,
        launcher=None,
        foreign_toplevel_protocol: object | None = None,
        workspace_protocol: object | None = None,
        screen_capture: ScreenCaptureService | None = None,
        protocol_runtime: WaylandProtocolRuntime | None = None,
    ) -> None:
        runtime = protocol_runtime
        if runtime is None and (
            foreign_toplevel_protocol is None or workspace_protocol is None
        ):
            candidate_runtime = WaylandProtocolRuntime()
            if candidate_runtime.start():
                runtime = candidate_runtime
        foreign_protocol = (
            foreign_toplevel_protocol
            if foreign_toplevel_protocol is not None
            else (
                runtime.foreign_toplevel_protocol
                if runtime is not None
                else load_foreign_toplevel_protocol()
            )
        )
        workspace_protocol = (
            workspace_protocol
            if workspace_protocol is not None
            else (
                runtime.workspace_protocol
                if runtime is not None
                else load_workspace_protocol()
            )
        )
        preview_protocol = (
            getattr(runtime, "preview_protocol", None) if runtime is not None else None
        )
        hyprland_preview_protocol = (
            getattr(runtime, "hyprland_preview_protocol", None)
            if runtime is not None
            else None
        )
        windows: WindowService
        preview_handles: WaylandPreviewHandleTracker | None = None
        hyprland_windows = (
            load_hyprland_window_service(model=model, launcher=launcher)
            if model is not None and launcher is not None
            else None
        )
        if hyprland_windows is not None:
            windows = hyprland_windows
            if (
                hyprland_preview_protocol is not None
                and foreign_protocol is not None
                and model is not None
                and launcher is not None
            ):
                hyprland_windows.set_preview_handle_source(
                    WaylandForeignToplevelWindowService(
                        model=model,
                        launcher=launcher,
                        protocol=foreign_protocol,
                        can_preview=True,
                    )
                )
        elif (
            foreign_protocol is not None and model is not None and launcher is not None
        ):
            if preview_protocol is not None:
                preview_handles = WaylandPreviewHandleTracker(
                    model=model,
                    launcher=launcher,
                    protocol=preview_protocol,
                )
            windows = WaylandForeignToplevelWindowService(
                model=model,
                launcher=launcher,
                protocol=foreign_protocol,
                preview_handles=preview_handles,
                can_preview=preview_protocol is None
                and hyprland_preview_protocol is not None,
            )
        else:
            windows = ReducedWindowService()
        if preview_protocol is not None and preview_handles is not None:
            previews: PreviewService = WaylandPreviewService(
                protocol=preview_protocol,
                handles=preview_handles,
            )
        elif hyprland_preview_protocol is not None and isinstance(
            windows, (WaylandForeignToplevelWindowService, HyprlandWindowService)
        ):
            previews = HyprlandPreviewService(
                protocol=hyprland_preview_protocol,
                windows=windows,
            )
        else:
            previews = ReducedPreviewService()
        workspaces = (
            WaylandWorkspaceService(protocol=workspace_protocol)
            if workspace_protocol is not None
            else None
        )
        self._services = WaylandLayerShellRuntimeServices(
            windows=windows,
            previews=previews,
            surface=WaylandLayerShellSurfaceService(layer_shell=layer_shell),
            visibility=ReducedVisibilityService(),
            workspaces=workspaces,
            screen_capture=screen_capture
            if screen_capture is not None
            else load_portal_color_picker(),
            protocol_runtime=runtime,
        )

    @property
    def name(self) -> str:
        return "wayland-layer-shell"

    @property
    def display_server(self) -> DisplayServer:
        return DisplayServer.WAYLAND

    @property
    def capabilities(self) -> PlatformCapabilities:
        tracks_windows = isinstance(
            self._services.windows,
            (WaylandForeignToplevelWindowService, HyprlandWindowService),
        )
        tracks_geometry = isinstance(self._services.windows, HyprlandWindowService)
        supports_workspaces = isinstance(
            self._services.workspaces, WaylandWorkspaceService
        )
        supports_color_pick = isinstance(
            self._services.screen_capture, WaylandPortalColorPickerService
        )
        return PlatformCapabilities(
            tracks_windows=tracks_windows,
            tracks_active_window=tracks_windows,
            tracks_minimized=tracks_windows,
            tracks_maximized=tracks_windows,
            tracks_fullscreen=tracks_windows,
            supports_activate=tracks_windows,
            supports_minimize=tracks_windows,
            supports_close=tracks_windows,
            supports_window_menu=tracks_windows,
            tracks_window_geometry=tracks_geometry,
            tracks_window_workspace=tracks_geometry,
            supports_workspace_list=supports_workspaces,
            supports_workspace_switch=supports_workspaces,
            supports_screen_color_pick=supports_color_pick,
            supports_layer_shell=True,
            supports_screen_reservation=True,
            supports_input_region=True,
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
        return None

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

    def stop(self) -> None:
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
