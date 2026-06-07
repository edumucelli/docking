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

"""COSMIC session backend with native toplevel, workspace, overlap, and preview.

Backend selection
  |
  +-- COSMIC Wayland session ---------> CosmicSessionBackend
                                         |
                                         +-- WaylandLayerShellSurfaceService
                                         +-- WaylandForeignToplevelWindowService
                                         |     (via CosmicToplevelAdapter)
                                         +-- WaylandWorkspaceService
                                         |     (via ext_workspace_manager_v1)
                                         +-- CosmicOverlapVisibilityService
                                         |     (native overlap-notify protocol)
                                         +-- WaylandPreviewService
                                         |     (via ext_image_copy_capture
                                         |      + ext_foreign_toplevel_
                                         |      image_capture_source)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from docking.platform.backends.base import (
    DesktopActionService,
    DisplayServer,
    IdleService,
    PlatformCapabilities,
    PreviewService,
    Rect,
    ScreenCaptureService,
    SessionBackend,
    SurfaceService,
    VisibilityMonitor,
    VisibilityService,
    WindowPickService,
    WindowService,
    WorkspaceService,
)
from docking.platform.backends.reduced.services import (
    ReducedPreviewService,
)
from docking.platform.backends.wayland.portals import (
    load_portal_color_picker,
)
from docking.platform.backends.wayland.previews import (
    WaylandPreviewHandleTracker,
    WaylandPreviewService,
)
from docking.platform.backends.wayland.runtime import WaylandProtocolRuntime
from docking.platform.backends.wayland.services import WaylandLayerShellSurfaceService
from docking.platform.backends.wayland.toplevels import (
    WaylandForeignToplevelWindowService,
)

if TYPE_CHECKING:
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel


@dataclass(frozen=True)
class CosmicRuntimeServices:
    """Concrete services selected for COSMIC native Wayland support."""

    windows: WindowService
    previews: PreviewService
    surface: WaylandLayerShellSurfaceService
    visibility: VisibilityService
    workspaces: WorkspaceService | None
    screen_capture: ScreenCaptureService | None
    protocol_runtime: WaylandProtocolRuntime | None


class CosmicOverlapVisibilityService(VisibilityService):
    """VisibilityService backed by COSMIC zcosmic_overlap_notify_v1."""

    def __init__(self, *, overlap_adapter: object) -> None:
        self._overlap_adapter = overlap_adapter
        self._monitors: list[CosmicOverlapMonitor] = []

    def start(self) -> None:
        """No global state to start; monitors start individually."""

    def stop(self) -> None:
        """Stop all active monitors."""
        for monitor in tuple(self._monitors):
            monitor.stop()
        self._monitors.clear()

    def create_monitor(
        self,
        *,
        get_dock_rect: Callable[[], Rect | None],
        on_change: Callable[[bool], None],
    ) -> VisibilityMonitor | None:
        """Create a COSMIC overlap monitor for the layer-shell surface."""
        monitor = CosmicOverlapMonitor(
            adapter=self._overlap_adapter,
            on_change=on_change,
        )
        self._monitors.append(monitor)
        return monitor


class CosmicOverlapMonitor(VisibilityMonitor):
    """Monitors COSMIC overlap notifications for dodge-driven visibility."""

    def __init__(
        self,
        *,
        adapter: object,
        on_change: Callable[[bool], None],
    ) -> None:
        self._adapter = adapter
        self._on_change = on_change
        self._started = False

    def start(self) -> None:
        """The actual subscription happens when a layer surface is available."""
        self._started = True

    def stop(self) -> None:
        """Stop overlap monitoring."""
        if self._started:
            self._adapter.stop()
        self._started = False

    def evaluate_now(self) -> None:
        """Overlap evaluation is push-based from the compositor."""
        self._adapter.evaluate_now()

    def attach_layer_surface(self, layer_surface: object) -> None:
        """Subscribe to overlap notifications on a layer-shell surface."""
        if not self._started:
            self.start()
        self._adapter.start(layer_surface, self._on_change)


class CosmicSessionBackend(SessionBackend):
    """SessionBackend with COSMIC protocols for toplevel, workspace, and overlap."""

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
            candidate_runtime = WaylandProtocolRuntime()
            if candidate_runtime.start():
                runtime = candidate_runtime

        cosmic_toplevel = (
            runtime.cosmic_toplevel_protocol if runtime is not None else None
        )
        cosmic_workspace = (
            runtime.cosmic_workspace_protocol if runtime is not None else None
        )
        cosmic_overlap = (
            runtime.cosmic_overlap_protocol if runtime is not None else None
        )
        preview_protocol = (
            runtime.preview_protocol if runtime is not None else None
        )
        hyprland_preview_protocol = (
            runtime.hyprland_preview_protocol if runtime is not None else None
        )

        # Preview handle tracker for window preview capture
        preview_handles: WaylandPreviewHandleTracker | None = None
        if preview_protocol is not None:
            preview_handles = WaylandPreviewHandleTracker(
                model=model,
                launcher=launcher,
                protocol=preview_protocol,
            )

        # Window service backed by COSMIC toplevel adapter
        windows: WindowService
        if cosmic_toplevel is not None:
            # COSMIC toplevel adapter handles both ext_foreign_toplevel_list_v1
            # (for listing) and zcosmic_toplevel_info_v1 (for richer info)
            windows = WaylandForeignToplevelWindowService(
                model=model,
                launcher=launcher,
                protocol=cosmic_toplevel,
                preview_handles=preview_handles,
                can_preview=preview_protocol is None
                and hyprland_preview_protocol is not None,
            )
        else:
            # Fall back to generic foreign-toplevel if available
            generic_toplevel = (
                runtime.foreign_toplevel_protocol if runtime is not None else None
            )
            if generic_toplevel is not None:
                windows = WaylandForeignToplevelWindowService(
                    model=model,
                    launcher=launcher,
                    protocol=generic_toplevel,
                    preview_handles=preview_handles,
                )
            else:
                from docking.platform.backends.reduced.services import (
                    ReducedWindowService,
                )
                windows = ReducedWindowService()

        # Workspace service: prefer ext_workspace_manager_v1 (standard) on COSMIC
        # since it works reliably. The COSMIC-specific zcosmic_workspace_manager_v2
        # binding is available but the compositor's protocol structure has diverged
        # from the XML used to generate the bindings.
        workspaces: WorkspaceService | None = None
        std_workspace = runtime.workspace_protocol if runtime is not None else None
        if std_workspace is not None:
            from docking.platform.backends.wayland.workspaces import (
                WaylandWorkspaceService,
            )
            workspaces = WaylandWorkspaceService(protocol=std_workspace)
        elif cosmic_workspace is not None:
            from docking.platform.backends.wayland.workspaces import (
                WaylandWorkspaceService,
            )
            workspaces = WaylandWorkspaceService(protocol=cosmic_workspace)

        # Visibility service backed by COSMIC overlap notify
        visibility: VisibilityService
        if cosmic_overlap is not None:
            visibility = CosmicOverlapVisibilityService(
                overlap_adapter=cosmic_overlap,
            )
        else:
            from docking.platform.backends.reduced.services import (
                ReducedVisibilityService,
            )
            visibility = ReducedVisibilityService()

        # Surface: always use layer-shell on COSMIC, with overlap binding
        def _on_layer_surface_ready(layer_surface: object) -> None:
            self._attach_overlap(layer_surface)

        surface = WaylandLayerShellSurfaceService(
            layer_shell=layer_shell,
            on_layer_surface_ready=_on_layer_surface_ready
            if cosmic_overlap is not None
            else None,
        )

        # Preview service: use standard Wayland capture when available
        previews: PreviewService
        if preview_protocol is not None and preview_handles is not None:
            previews = WaylandPreviewService(
                protocol=preview_protocol,
                handles=preview_handles,
            )
        elif hyprland_preview_protocol is not None and isinstance(
            windows, WaylandForeignToplevelWindowService
        ):
            from docking.platform.backends.wayland.previews import (
                HyprlandPreviewService,
            )
            previews = HyprlandPreviewService(
                protocol=hyprland_preview_protocol,
                windows=windows,
            )
        else:
            previews = ReducedPreviewService()

        self._services = CosmicRuntimeServices(
            windows=windows,
            previews=previews,
            surface=surface,
            visibility=visibility,
            workspaces=workspaces,
            screen_capture=screen_capture
            if screen_capture is not None
            else load_portal_color_picker(),
            protocol_runtime=runtime,
        )

        # Stash overlap adapter for late binding to the layer surface
        self._cosmic_overlap = cosmic_overlap

    @property
    def name(self) -> str:
        return "cosmic"

    @property
    def display_server(self) -> DisplayServer:
        return DisplayServer.WAYLAND

    @property
    def capabilities(self) -> PlatformCapabilities:
        from docking.platform.backends.wayland.toplevels import (
            WaylandForeignToplevelWindowService,
        )

        tracks_windows = isinstance(
            self._services.windows, WaylandForeignToplevelWindowService
        )
        supports_workspaces = self._services.workspaces is not None
        supports_overlap = isinstance(
            self._services.visibility, CosmicOverlapVisibilityService
        )
        return PlatformCapabilities(
            tracks_windows=tracks_windows,
            tracks_active_window=tracks_windows,
            tracks_minimized=tracks_windows,
            tracks_maximized=tracks_windows,
            tracks_fullscreen=tracks_windows,
            tracks_window_geometry=tracks_windows,
            supports_activate=tracks_windows,
            supports_minimize=tracks_windows,
            supports_close=tracks_windows,
            supports_window_menu=tracks_windows,
            supports_workspace_list=supports_workspaces,
            supports_workspace_switch=supports_workspaces,
            supports_layer_shell=True,
            supports_screen_reservation=True,
            supports_input_region=True,
            supports_overlap_active=supports_overlap,
            supports_overlap_any=supports_overlap,
            supports_overlap_maximized=supports_overlap,
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

    def _attach_overlap(self, layer_surface: object) -> None:
        """Bind the overlap notification to the realized layer surface."""
        if self._cosmic_overlap is None:
            return
        visibility = self._services.visibility
        if not isinstance(visibility, CosmicOverlapVisibilityService):
            return
        for monitor in visibility._monitors:
            if isinstance(monitor, CosmicOverlapMonitor) and not monitor._started:
                monitor.attach_layer_surface(layer_surface)
