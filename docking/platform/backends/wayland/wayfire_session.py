# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Wayfire session backend with layer-shell surfaces and IPC window state."""

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
from docking.platform.backends.wayland.portals import (
    WaylandPortalColorPickerService,
    load_portal_color_picker,
)
from docking.platform.backends.wayland.runtime import WaylandProtocolRuntime
from docking.platform.backends.wayland.services import WaylandLayerShellSurfaceService
from docking.platform.backends.wayland.wayfire_ipc import (
    WayfireWindowPickService,
    WayfireWindowService,
    load_wayfire_window_pick_service,
    load_wayfire_window_service,
    load_wayfire_workspace_service,
)

if TYPE_CHECKING:
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel


@dataclass(frozen=True)
class WayfireRuntimeServices:
    """Concrete services selected for the Wayfire Wayland backend."""

    windows: WindowService
    previews: PreviewService
    surface: WaylandLayerShellSurfaceService
    visibility: ReducedVisibilityService
    workspaces: WorkspaceService | None
    screen_capture: ScreenCaptureService | None
    window_picker: WindowPickService | None
    protocol_runtime: WaylandProtocolRuntime | None


class WayfireSessionBackend(SessionBackend):
    """SessionBackend using Wayfire IPC for windows and layer-shell surfaces."""

    def __init__(
        self,
        *,
        layer_shell: object,
        model: DockModel,
        launcher: Launcher,
        protocol_runtime: WaylandProtocolRuntime | None = None,
        screen_capture: ScreenCaptureService | None = None,
        window_service: WindowService | None = None,
        workspace_service: WorkspaceService | None = None,
        window_picker: WindowPickService | None = None,
    ) -> None:
        runtime = protocol_runtime
        if runtime is None:
            candidate_runtime = WaylandProtocolRuntime()
            if candidate_runtime.start():
                runtime = candidate_runtime

        windows = window_service or load_wayfire_window_service(
            model=model,
            launcher=launcher,
        )
        if windows is None:
            windows = ReducedWindowService()

        workspaces = workspace_service or load_wayfire_workspace_service()
        picker = window_picker or load_wayfire_window_pick_service()

        self._services = WayfireRuntimeServices(
            windows=windows,
            previews=ReducedPreviewService(),
            surface=WaylandLayerShellSurfaceService(layer_shell=layer_shell),
            visibility=ReducedVisibilityService(),
            workspaces=workspaces,
            screen_capture=screen_capture
            if screen_capture is not None
            else load_portal_color_picker(),
            window_picker=picker,
            protocol_runtime=runtime,
        )

    @property
    def name(self) -> str:
        return "wayfire"

    @property
    def display_server(self) -> DisplayServer:
        return DisplayServer.WAYLAND

    @property
    def capabilities(self) -> PlatformCapabilities:
        tracks_windows = isinstance(self._services.windows, WayfireWindowService)
        supports_workspaces = self._services.workspaces is not None
        supports_color_pick = isinstance(
            self._services.screen_capture,
            WaylandPortalColorPickerService,
        )
        supports_window_pick = isinstance(
            self._services.window_picker,
            WayfireWindowPickService,
        )
        # Wayfire ipc-rules gives us view snapshots, focus/close actions, output
        # workspace grids, geometry, PID, and layer-shell via gtk-layer-shell.
        # It does not expose urgent/attention state, preview pixels,
        # EWMH-style maximize state, pointer barriers, blur hints, idle time, or
        # a workspace-switch action in the method set used here. Overlap/dodge is
        # also left false until we add a config-aware Wayfire visibility monitor.
        return PlatformCapabilities(
            tracks_windows=tracks_windows,
            tracks_active_window=tracks_windows,
            tracks_minimized=tracks_windows,
            tracks_fullscreen=tracks_windows,
            supports_activate=tracks_windows,
            supports_minimize=False,
            supports_close=tracks_windows,
            supports_window_menu=tracks_windows,
            tracks_window_geometry=tracks_windows,
            tracks_window_workspace=tracks_windows,
            supports_current_workspace_filter=False,
            supports_workspace_list=supports_workspaces,
            supports_workspace_switch=False,
            supports_layer_shell=True,
            supports_screen_reservation=True,
            supports_input_region=True,
            supports_screen_color_pick=supports_color_pick,
            supports_window_pick=supports_window_pick,
            supports_window_pid=supports_window_pick,
            supports_process_kill=supports_window_pick,
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
        return self._services.window_picker

    def start(self) -> None:
        self._services.previews.start()
        self._services.windows.start()
        self._services.surface.start()
        self._services.visibility.start()
        if self._services.workspaces is not None:
            self._services.workspaces.start()
        if self._services.screen_capture is not None:
            self._services.screen_capture.start()
        if self._services.window_picker is not None:
            self._services.window_picker.start()

    def stop(self) -> None:
        if self._services.window_picker is not None:
            self._services.window_picker.stop()
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
