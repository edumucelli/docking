"""Treeland session composed from generic Wayland services plus DDE extensions."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from docking.platform.backends.base import (
    DesktopActionService,
    PlatformCapabilities,
    VisibilityService,
)
from docking.platform.backends.wayland.session import WaylandLayerShellSessionBackend
from docking.platform.backends.wayland.treeland import (
    TreelandDesktopActionService,
    TreelandVisibilityService,
)

if TYPE_CHECKING:
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.backends.base import ScreenCaptureService
    from docking.platform.backends.wayland.runtime import WaylandProtocolRuntime
    from docking.platform.model import DockModel


class TreelandSessionBackend(WaylandLayerShellSessionBackend):
    """Decorate the generic backend with overlap and Show Desktop support."""

    def __init__(
        self,
        *,
        layer_shell: object,
        model: DockModel,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
        protocol_runtime: WaylandProtocolRuntime | None = None,
        screen_capture: ScreenCaptureService | None = None,
    ) -> None:
        super().__init__(
            layer_shell=layer_shell,
            model=model,
            application_registry=application_registry,
            process_identity_service=process_identity_service,
            protocol_runtime=protocol_runtime,
            screen_capture=screen_capture,
        )
        runtime = self._services.protocol_runtime
        overlap = runtime.treeland_overlap_protocol if runtime is not None else None
        window_management = (
            runtime.treeland_window_management_protocol if runtime is not None else None
        )
        self._treeland_visibility = (
            TreelandVisibilityService(adapter=overlap) if overlap is not None else None
        )
        self._treeland_desktop_actions = (
            TreelandDesktopActionService(adapter=window_management)
            if window_management is not None
            else None
        )

    @property
    def name(self) -> str:
        return "treeland"

    @property
    def capabilities(self) -> PlatformCapabilities:
        base = super().capabilities
        return replace(
            base,
            supports_show_desktop=self._treeland_desktop_actions is not None,
            supports_overlap_any=self._treeland_visibility is not None,
        )

    @property
    def visibility(self) -> VisibilityService:
        return self._treeland_visibility or super().visibility

    @property
    def desktop_actions(self) -> DesktopActionService | None:
        return self._treeland_desktop_actions

    def start(self) -> None:
        super().start()
        if self._treeland_visibility is not None:
            self._treeland_visibility.start()
        if self._treeland_desktop_actions is not None:
            self._treeland_desktop_actions.start()

    def stop(self) -> None:
        if self._treeland_desktop_actions is not None:
            self._treeland_desktop_actions.stop()
        if self._treeland_visibility is not None:
            self._treeland_visibility.stop()
        super().stop()
