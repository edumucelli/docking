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
from docking.platform.backends.wayland.idle import WaylandIdleService
from docking.platform.backends.wayland.portals import (
    WaylandPortalColorPickerService,
    load_portal_color_picker,
)
from docking.platform.backends.wayland.previews import (
    HyprlandPreviewService,
    PhocPreviewService,
    WaylandPreviewHandleTracker,
    WaylandPreviewService,
)
from docking.platform.backends.wayland.runtime import (
    GENERIC_LAYER_SHELL_PROTOCOL_PROFILE,
    WaylandProtocolProfile,
    WaylandProtocolRuntime,
)
from docking.platform.backends.wayland.services import WaylandLayerShellSurfaceService
from docking.platform.backends.wayland.toplevels import (
    WaylandForeignToplevelWindowService,
)
from docking.platform.backends.wayland.workspaces import WaylandWorkspaceService

if TYPE_CHECKING:
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel


class WaylandLayerShellSessionBackend(ComposedWaylandSessionBackend):
    """SessionBackend with native Wayland surface placement and optional windows."""

    protocol_profile: WaylandProtocolProfile = GENERIC_LAYER_SHELL_PROTOCOL_PROFILE

    def __init__(
        self,
        *,
        layer_shell: object,
        model: DockModel,
        launcher: Launcher,
        screen_capture: ScreenCaptureService | None = None,
        protocol_runtime: WaylandProtocolRuntime | None = None,
    ) -> None:
        runtime = protocol_runtime
        if runtime is None:
            candidate_runtime = WaylandProtocolRuntime(profile=self.protocol_profile)
            if candidate_runtime.start():
                runtime = candidate_runtime
        foreign_protocol = (
            runtime.foreign_toplevel_protocol if runtime is not None else None
        )
        workspace_protocol = runtime.workspace_protocol if runtime is not None else None
        preview_protocol = (
            getattr(runtime, "preview_protocol", None) if runtime is not None else None
        )
        hyprland_preview_protocol = (
            getattr(runtime, "hyprland_preview_protocol", None)
            if runtime is not None
            else None
        )
        phoc_preview_protocol = (
            getattr(runtime, "phoc_preview_protocol", None)
            if runtime is not None
            else None
        )
        windows: WindowService
        preview_handles: WaylandPreviewHandleTracker | None = None
        if foreign_protocol is not None:
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
                and (
                    hyprland_preview_protocol is not None
                    or phoc_preview_protocol is not None
                ),
            )
        else:
            windows = ReducedWindowService()
        if preview_protocol is not None and preview_handles is not None:
            previews: PreviewService = WaylandPreviewService(
                protocol=preview_protocol,
                handles=preview_handles,
            )
        elif hyprland_preview_protocol is not None and isinstance(
            windows, WaylandForeignToplevelWindowService
        ):
            previews = HyprlandPreviewService(
                protocol=hyprland_preview_protocol,
                windows=windows,
            )
        elif phoc_preview_protocol is not None and isinstance(
            windows, WaylandForeignToplevelWindowService
        ):
            previews = PhocPreviewService(
                protocol=phoc_preview_protocol,
                windows=windows,
            )
        else:
            previews = ReducedPreviewService()
        workspaces = (
            WaylandWorkspaceService(protocol=workspace_protocol)
            if workspace_protocol is not None
            else None
        )
        idle_protocol = (
            getattr(runtime, "idle_protocol", None) if runtime is not None else None
        )
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
        return "wayland-layer-shell"

    @property
    def capabilities(self) -> PlatformCapabilities:
        tracks_windows = isinstance(
            self._services.windows, WaylandForeignToplevelWindowService
        )
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
            supports_workspace_list=supports_workspaces,
            supports_workspace_switch=supports_workspaces,
            supports_screen_color_pick=supports_color_pick,
            supports_layer_shell=True,
            supports_screen_reservation=True,
            supports_input_region=True,
            supports_idle_time=isinstance(self._services.idle, WaylandIdleService),
        )
