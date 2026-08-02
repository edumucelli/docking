# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Wayfire session backend with layer-shell surfaces and IPC window state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from docking.platform.backends.base import (
    DesktopActionService,
    IdleService,
    PlatformCapabilities,
    PreviewService,
    ScreenCaptureService,
    VisibilityService,
    WindowPickService,
    WindowService,
    WorkspaceService,
)
from docking.platform.backends.reduced.services import (
    ReducedVisibilityService,
    ReducedWindowService,
)
from docking.platform.backends.wayland.composed_session import (
    ComposedWaylandSessionBackend,
    WaylandSessionServices,
)
from docking.platform.backends.wayland.idle import WaylandIdleService
from docking.platform.backends.wayland.portals import (
    WaylandPortalColorPickerService,
    load_portal_color_picker,
)
from docking.platform.backends.wayland.runtime import (
    STANDARD_PROTOCOL_PROFILE,
    WaylandProtocolRuntime,
)
from docking.platform.backends.wayland.services import WaylandLayerShellSurfaceService
from docking.platform.backends.wayland.wayfire_ipc import (
    WayfireDesktopActionService,
    WayfireVisibilityService,
    WayfireWindowPickService,
    WayfireWindowService,
    load_wayfire_desktop_action_service,
    load_wayfire_preview_service,
    load_wayfire_visibility_service,
    load_wayfire_window_pick_service,
    load_wayfire_window_service,
    load_wayfire_workspace_service,
)

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel


class WayfireSessionBackend(ComposedWaylandSessionBackend):
    """SessionBackend using Wayfire IPC for windows and layer-shell surfaces."""

    def __init__(
        self,
        *,
        layer_shell: object,
        model: DockModel,
        launcher: Launcher,
        config: Config,
        protocol_runtime: WaylandProtocolRuntime | None = None,
        screen_capture: ScreenCaptureService | None = None,
        window_service: WindowService | None = None,
        workspace_service: WorkspaceService | None = None,
        window_picker: WindowPickService | None = None,
        visibility_service: VisibilityService | None = None,
        desktop_action_service: DesktopActionService | None = None,
        preview_service: PreviewService | None = None,
    ) -> None:
        runtime = protocol_runtime
        if runtime is None:
            candidate_runtime = WaylandProtocolRuntime(
                profile=STANDARD_PROTOCOL_PROFILE
            )
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
        desktop_actions = (
            desktop_action_service or load_wayfire_desktop_action_service()
        )

        visibility = visibility_service
        if visibility is None:
            visibility = load_wayfire_visibility_service(config=config)
        if visibility is None:
            visibility = ReducedVisibilityService()

        previews = preview_service or load_wayfire_preview_service()
        if previews is None:
            from docking.platform.backends.reduced.services import (
                ReducedPreviewService,
            )

            previews = ReducedPreviewService()
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
                visibility=visibility,
                workspaces=workspaces,
                desktop_actions=desktop_actions,
                screen_capture=screen_capture
                if screen_capture is not None
                else load_portal_color_picker(),
                idle=idle,
                window_picker=picker,
                protocol_runtime=runtime,
            )
        )

    @property
    def name(self) -> str:
        return "wayfire"

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
        supports_visibility = isinstance(
            self._services.visibility,
            WayfireVisibilityService,
        )
        supports_show_desktop = isinstance(
            self._services.desktop_actions,
            WayfireDesktopActionService,
        )
        # Wayfire list-views returns bottom-to-top stacking order, validated
        # by focusing different windows and verifying position stability.
        # wm-actions and vswitch give us minimize, fullscreen, show-desktop,
        # and workspace switching.  Overlap-maximized is handled by the
        # visibility monitor checking fullscreen views against the dock rect.
        # Not yet exposed: urgent/attention state, EWMH maximize state,
        # pointer barriers, blur hints, idle time, and per-window workspace
        # coordinates for current-workspace filtering.
        return PlatformCapabilities(
            tracks_windows=tracks_windows,
            tracks_active_window=tracks_windows,
            tracks_minimized=tracks_windows,
            tracks_fullscreen=tracks_windows,
            tracks_stacking_order=tracks_windows,
            supports_activate=tracks_windows,
            supports_minimize=tracks_windows,
            supports_close=tracks_windows,
            supports_window_menu=tracks_windows,
            tracks_window_geometry=tracks_windows,
            tracks_window_workspace=tracks_windows,
            supports_current_workspace_filter=False,
            supports_workspace_list=supports_workspaces,
            supports_workspace_switch=supports_workspaces,
            supports_show_desktop=supports_show_desktop,
            supports_layer_shell=True,
            supports_screen_reservation=True,
            supports_input_region=True,
            supports_overlap_active=supports_visibility,
            supports_overlap_any=supports_visibility,
            supports_overlap_maximized=supports_visibility,
            supports_screen_color_pick=supports_color_pick,
            supports_window_pick=supports_window_pick,
            supports_window_pid=supports_window_pick,
            supports_process_kill=supports_window_pick,
            supports_idle_time=isinstance(self._services.idle, WaylandIdleService),
        )
