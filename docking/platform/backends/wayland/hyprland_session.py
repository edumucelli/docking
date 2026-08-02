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

from typing import TYPE_CHECKING

from docking.platform.backends.base import (
    IdleService,
    PlatformCapabilities,
    PreviewService,
    ScreenCaptureService,
    WindowService,
)
from docking.platform.backends.reduced.services import (
    ReducedPreviewService,
    ReducedVisibilityService,
    ReducedWindowService,
)
from docking.platform.backends.wayland.composed_session import (
    ComposedWaylandSessionBackend,
    WaylandSessionServices,
)
from docking.platform.backends.wayland.hyprland_ipc import (
    HyprlandWindowService,
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
from docking.platform.backends.wayland.runtime import (
    HYPRLAND_PROTOCOL_PROFILE,
    WaylandProtocolRuntime,
)
from docking.platform.backends.wayland.services import WaylandLayerShellSurfaceService
from docking.platform.backends.wayland.workspaces import WaylandWorkspaceService

if TYPE_CHECKING:
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel


class HyprlandSessionBackend(ComposedWaylandSessionBackend):
    """SessionBackend using Hyprland IPC for windows and layer-shell surfaces."""

    def __init__(
        self,
        *,
        layer_shell: object,
        model: DockModel,
        launcher: Launcher,
        protocol_runtime: WaylandProtocolRuntime | None = None,
        screen_capture: ScreenCaptureService | None = None,
        window_service: WindowService | None = None,
    ) -> None:
        runtime = protocol_runtime
        if runtime is None:
            candidate_runtime = WaylandProtocolRuntime(
                profile=HYPRLAND_PROTOCOL_PROFILE
            )
            if candidate_runtime.start():
                runtime = candidate_runtime

        windows = window_service or load_hyprland_window_service(
            model=model,
            launcher=launcher,
        )
        if windows is None:
            windows = ReducedWindowService()

        foreign_toplevel = (
            runtime.foreign_toplevel_protocol if runtime is not None else None
        )
        preview_handles = None
        if isinstance(windows, HyprlandWindowService) and foreign_toplevel is not None:
            preview_handles = WaylandPreviewHandleTracker(
                model=model,
                launcher=launcher,
                protocol=foreign_toplevel,
            )
            windows.set_preview_handle_source(preview_handles)

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
            WaylandWorkspaceService(protocol=workspace_protocol)
            if workspace_protocol is not None
            else None
        )
        idle_protocol = runtime.idle_protocol if runtime is not None else None
        idle: IdleService | None = (
            WaylandIdleService(protocol=idle_protocol)
            if idle_protocol is not None
            else None
        )

        super().__init__(
            services=WaylandSessionServices(
                windows=windows,
                previews=previews,
                surface=WaylandLayerShellSurfaceService(layer_shell=layer_shell),
                visibility=ReducedVisibilityService(),
                workspaces=workspaces,
                screen_capture=screen_capture
                if screen_capture is not None
                else load_portal_color_picker(),
                idle=idle,
                protocol_runtime=runtime,
            )
        )

    @property
    def name(self) -> str:
        return "hyprland"

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
            tracks_minimized=tracks_windows,
            tracks_fullscreen=tracks_windows,
            supports_activate=tracks_windows,
            supports_minimize=tracks_windows,
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
            supports_idle_time=isinstance(self._services.idle, WaylandIdleService),
        )
