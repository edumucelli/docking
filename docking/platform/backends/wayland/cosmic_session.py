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

"""COSMIC session backend with native windows, workspace-aware dodge, and preview.

Backend selection
  |
  +-- COSMIC Wayland session ---------> CosmicSessionBackend
                                         |
                                         +-- WaylandLayerShellSurfaceService
                                         +-- WaylandForeignToplevelWindowService
                                         |     (via CosmicToplevelAdapter)
                                         +-- WaylandWorkspaceService
                                         |     (via ext_workspace_manager_v1)
                                         +-- SnapshotVisibilityService
                                         |     (native geometry and workspaces)
                                         +-- WaylandPreviewService
                                         |     (via ext_image_copy_capture
                                         |      + ext_foreign_toplevel_
                                         |      image_capture_source)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
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
    WindowSnapshot,
    WorkspaceService,
)
from docking.platform.backends.reduced.services import (
    ReducedPreviewService,
)
from docking.platform.backends.visibility import SnapshotVisibilityService
from docking.platform.backends.wayland.idle import WaylandIdleService
from docking.platform.backends.wayland.portals import (
    WaylandPortalColorPickerService,
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
    _ToplevelState,
)

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.backends.wayland.cosmic import (
        CosmicOverlapAdapter,
        CosmicToplevelAdapter,
    )
    from docking.platform.model import DockModel


class CosmicWindowService(WaylandForeignToplevelWindowService):
    """COSMIC window facts and taskbar scope from native workspace membership."""

    def __init__(
        self,
        *,
        config: Config | None,
        workspaces: WorkspaceService | None,
        protocol: CosmicToplevelAdapter,
        **kwargs,
    ) -> None:
        super().__init__(protocol=protocol, **kwargs)
        self._config = config
        self._workspaces = workspaces
        self._cosmic_protocol = protocol
        self._workspace_watch: object | None = None

    @property
    def supports_workspace_visibility(self) -> bool:
        return self._workspaces is not None and self._cosmic_protocol.supports_workspace

    def start(self) -> None:
        super().start()
        if self._workspaces is not None and self._workspace_watch is None:
            self._workspace_watch = self._workspaces.watch_active_workspace(
                self.refresh
            )

    def stop(self) -> None:
        if self._workspaces is not None and self._workspace_watch is not None:
            self._workspaces.unwatch_active_workspace(self._workspace_watch)
        self._workspace_watch = None
        super().stop()

    def _on_current_workspace(self, state: _ToplevelState) -> bool | None:
        if not self.supports_workspace_visibility:
            return None
        if state.sticky:
            return True
        memberships = self._cosmic_protocol.workspace_ids_for_handle(state.handle)
        if memberships is None or self._workspaces is None:
            return None
        active = {row.id for row in self._workspaces.list_workspaces() if row.active}
        if not active:
            return None
        return bool(memberships & active)

    def _taskbar_state(self, state: _ToplevelState) -> bool:
        return (
            not self.supports_workspace_visibility
            or self._config is None
            or not self._config.current_workspace_only
            or self._on_current_workspace(state) is True
        )

    def _snapshot_for(self, state: _ToplevelState) -> WindowSnapshot:
        current = self._on_current_workspace(state)
        visible = (
            bool(state.outputs) and current and state.minimized is False
            if current is not None and state.minimized is not None
            else None
        )
        return replace(
            super()._snapshot_for(state),
            on_current_workspace=current,
            visible=visible,
        )


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
    idle: IdleService | None


class CosmicOverlapVisibilityService(VisibilityService):
    """Overlap for surfaces owned by the adapter's Wayland connection.

    GTK layer surfaces use another connection, so CosmicSessionBackend selects
    native window geometry instead of sending GTK objects to this adapter.
    """

    def __init__(self, *, overlap_adapter: CosmicOverlapAdapter) -> None:
        self._overlap_adapter = overlap_adapter
        self._monitors: list[CosmicOverlapMonitor] = []
        self._layer_surface: object | None = None

    def start(self) -> None:
        """No global state to start; monitors start individually."""

    def stop(self) -> None:
        """Stop all active monitors."""
        for monitor in tuple(self._monitors):
            monitor.stop()
        self._monitors.clear()
        self._layer_surface = None

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
        if self._layer_surface is not None:
            monitor.attach_layer_surface(self._layer_surface)
        return monitor

    def attach_layer_surface(self, layer_surface: object) -> None:
        """Make the realized layer surface available to current and future monitors."""
        self._layer_surface = layer_surface
        for monitor in tuple(self._monitors):
            monitor.attach_layer_surface(layer_surface)


class CosmicOverlapMonitor(VisibilityMonitor):
    """Monitors COSMIC overlap notifications for dodge-driven visibility."""

    def __init__(
        self,
        *,
        adapter: CosmicOverlapAdapter,
        on_change: Callable[[bool], None],
    ) -> None:
        self._adapter = adapter
        self._on_change = on_change
        self._started = False
        self._layer_surface: object | None = None

    def start(self) -> None:
        """The actual subscription happens when a layer surface is available."""
        self._started = True
        self._subscribe_if_ready()

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
        self._layer_surface = layer_surface
        self._subscribe_if_ready()

    def _subscribe_if_ready(self) -> None:
        if self._started and self._layer_surface is not None:
            self._adapter.start(self._layer_surface, self._on_change)


class CosmicSessionBackend(SessionBackend):
    """SessionBackend with COSMIC protocols for toplevel, workspace, and overlap."""

    def __init__(
        self,
        *,
        layer_shell: object,
        model: DockModel,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
        protocol_runtime: WaylandProtocolRuntime | None = None,
        screen_capture: ScreenCaptureService | None = None,
        config: Config | None = None,
    ) -> None:
        runtime = protocol_runtime
        if runtime is None:
            candidate_runtime = WaylandProtocolRuntime()
            if candidate_runtime.start():
                runtime = candidate_runtime

        cosmic_toplevel = (
            runtime.cosmic_toplevel_protocol if runtime is not None else None
        )
        preview_protocol = runtime.preview_protocol if runtime is not None else None
        hyprland_preview_protocol = (
            runtime.hyprland_preview_protocol if runtime is not None else None
        )

        # Workspace services start before window replay, so window snapshots use
        # the same stable IDs and all active outputs' workspace memberships.
        std_workspace = runtime.workspace_protocol if runtime is not None else None
        from docking.platform.backends.wayland.workspaces import WaylandWorkspaceService

        workspaces: WorkspaceService | None = (
            WaylandWorkspaceService(protocol=std_workspace)
            if std_workspace is not None
            else None
        )

        # Preview handle tracker for window preview capture
        preview_handles: WaylandPreviewHandleTracker | None = None
        if preview_protocol is not None:
            preview_handles = WaylandPreviewHandleTracker(
                model=model,
                application_registry=application_registry,
                process_identity_service=process_identity_service,
                protocol=preview_protocol,
            )

        # Window service backed by COSMIC toplevel adapter
        windows: WindowService
        if cosmic_toplevel is not None:
            # COSMIC toplevel adapter handles both ext_foreign_toplevel_list_v1
            # (for listing) and zcosmic_toplevel_info_v1 (for richer info)
            windows = CosmicWindowService(
                config=config,
                workspaces=workspaces,
                model=model,
                application_registry=application_registry,
                process_identity_service=process_identity_service,
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
                    application_registry=application_registry,
                    process_identity_service=process_identity_service,
                    protocol=generic_toplevel,
                    preview_handles=preview_handles,
                )
            else:
                from docking.platform.backends.reduced.services import (
                    ReducedWindowService,
                )

                windows = ReducedWindowService()

        # GTK owns the layer surface on a different Wayland connection. Sending
        # it to the PyWayland overlap manager is invalid. Native geometry and
        # workspace events instead supply all three dodge policies safely.
        visibility: VisibilityService
        if (
            isinstance(windows, CosmicWindowService)
            and cosmic_toplevel is not None
            and cosmic_toplevel.supports_geometry
            and windows.supports_workspace_visibility
        ):
            visibility = SnapshotVisibilityService(
                windows=windows,
                visible_windows=lambda _rect: windows.list_all_windows(),
                config=config,
            )
        else:
            from docking.platform.backends.reduced.services import (
                ReducedVisibilityService,
            )

            visibility = ReducedVisibilityService()

        surface = WaylandLayerShellSurfaceService(
            layer_shell=layer_shell,
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
            idle=WaylandIdleService(protocol=runtime.idle_protocol)
            if runtime is not None and runtime.idle_protocol is not None
            else None,
        )

        self._cosmic_toplevel = cosmic_toplevel

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
            self._services.visibility, SnapshotVisibilityService
        )
        cosmic = self._cosmic_toplevel
        tracks_state = tracks_windows and (cosmic is None or cosmic.supports_state)
        return PlatformCapabilities(
            supports_idle_time=self._services.idle is not None,
            supports_screen_color_pick=isinstance(
                self._services.screen_capture, WaylandPortalColorPickerService
            ),
            tracks_windows=tracks_windows,
            tracks_active_window=tracks_state,
            tracks_minimized=tracks_state,
            tracks_maximized=tracks_state,
            tracks_fullscreen=tracks_state,
            tracks_window_geometry=tracks_windows
            and self._cosmic_toplevel is not None
            and self._cosmic_toplevel.supports_geometry,
            tracks_window_workspace=tracks_windows
            and supports_workspaces
            and self._cosmic_toplevel is not None
            and self._cosmic_toplevel.supports_workspace,
            supports_current_workspace_filter=isinstance(
                self._services.windows, CosmicWindowService
            )
            and self._services.windows.supports_workspace_visibility,
            supports_activate=tracks_windows
            and (cosmic is None or cosmic.supports_action("activate", None)),
            supports_minimize=tracks_windows
            and (cosmic is None or cosmic.supports_action("set_minimized", None)),
            supports_close=tracks_windows
            and (cosmic is None or cosmic.supports_action("close", None)),
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
        return self._services.idle

    @property
    def window_picker(self) -> WindowPickService | None:
        return None

    def start(self) -> None:
        if self._services.idle is not None:
            self._services.idle.start()
        self._services.previews.start()
        if self._services.workspaces is not None:
            self._services.workspaces.start()
        self._services.windows.start()
        self._services.surface.start()
        self._services.visibility.start()
        if self._services.screen_capture is not None:
            self._services.screen_capture.start()

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
