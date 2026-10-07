"""Native Sway services with the existing layer-shell/portal/idle integration."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from docking.platform.backends.reduced.services import ReducedPreviewService
from docking.platform.backends.visibility import SnapshotVisibilityService
from docking.platform.backends.wayland.previews import (
    WaylandPreviewHandleTracker,
    WaylandPreviewService,
)
from docking.platform.backends.wayland.session import WaylandLayerShellSessionBackend
from docking.platform.backends.wayland.sway_ipc import (
    SwayIpcClient,
    SwayWindowService,
    SwayWorkspaceService,
)

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.backends.base import PlatformCapabilities


class SwaySessionBackend(WaylandLayerShellSessionBackend):
    def __init__(
        self, *, socket_path: str, config: Config | None = None, **kwargs
    ) -> None:
        super().__init__(**kwargs)
        client = SwayIpcClient(socket_path)
        runtime = self._services.protocol_runtime
        protocol = runtime.preview_protocol if runtime is not None else None
        handles = (
            WaylandPreviewHandleTracker(
                model=kwargs["model"],
                application_registry=kwargs["application_registry"],
                process_identity_service=kwargs["process_identity_service"],
                protocol=protocol,
            )
            if protocol is not None
            else None
        )
        windows = SwayWindowService(
            client=client,
            config=config,
            preview_handles=handles,
            model=kwargs["model"],
            application_registry=kwargs["application_registry"],
            process_identity_service=kwargs["process_identity_service"],
        )
        self._services = replace(
            self._services,
            windows=windows,
            workspaces=SwayWorkspaceService(client=client, windows=windows),
            visibility=SnapshotVisibilityService(
                windows=windows,
                visible_windows=lambda _rect: windows.list_all_windows(),
                config=config,
            ),
            # Sway container IDs are not foreign-toplevel handles. Never capture
            # an unrelated window by treating them as protocol IDs.
            previews=WaylandPreviewService(protocol=protocol, handles=handles)
            if protocol is not None and handles is not None
            else ReducedPreviewService(),
        )

    @property
    def name(self) -> str:
        return "sway"

    @property
    def capabilities(self) -> PlatformCapabilities:
        return replace(
            super().capabilities,
            tracks_windows=True,
            tracks_active_window=True,
            tracks_attention=True,
            tracks_minimized=False,
            tracks_fullscreen=True,
            tracks_maximized=False,
            tracks_window_geometry=True,
            tracks_window_workspace=True,
            supports_current_workspace_filter=True,
            supports_activate=True,
            supports_close=True,
            supports_minimize=False,
            supports_window_menu=True,
            supports_workspace_list=True,
            supports_workspace_switch=True,
            supports_overlap_active=True,
            supports_overlap_any=True,
        )
