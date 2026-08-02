# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Niri session backend with layer-shell surfaces and IPC window state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from docking.platform.backends.base import (
    IdleService,
    PlatformCapabilities,
    PreviewService,
    ScreenCaptureService,
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
from docking.platform.backends.wayland.runtime import (
    STANDARD_PROTOCOL_PROFILE,
    WaylandProtocolRuntime,
)
from docking.platform.backends.wayland.services import WaylandLayerShellSurfaceService
from docking.platform.backends.wayland.workspaces import WaylandWorkspaceService

if TYPE_CHECKING:
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel


class NiriSessionBackend(ComposedWaylandSessionBackend):
    """SessionBackend using Niri IPC for windows and layer-shell surfaces."""

    def __init__(
        self,
        *,
        layer_shell: object,
        model: DockModel,
        launcher: Launcher,
        protocol_runtime: WaylandProtocolRuntime | None = None,
        screen_capture: ScreenCaptureService | None = None,
    ) -> None:
        runtime = protocol_runtime
        if runtime is None:
            candidate_runtime = WaylandProtocolRuntime(
                profile=STANDARD_PROTOCOL_PROFILE
            )
            if candidate_runtime.start():
                runtime = candidate_runtime

        windows = load_niri_window_service(
            model=model,
            launcher=launcher,
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

        super().__init__(
            services=WaylandSessionServices(
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
        )

    @property
    def name(self) -> str:
        return "niri"

    @property
    def capabilities(self) -> PlatformCapabilities:
        tracks_windows = isinstance(self._services.windows, NiriWindowService)
        supports_workspaces = self._services.workspaces is not None
        supports_color_pick = isinstance(
            self._services.screen_capture,
            (WaylandPortalColorPickerService, NiriScreenCaptureService),
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
