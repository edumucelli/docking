"""Treeland session composed from generic Wayland services plus DDE extensions."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from docking.platform.backends.base import (
    PlatformCapabilities,
)
from docking.platform.backends.wayland.runtime import TREELAND_PROTOCOL_PROFILE
from docking.platform.backends.wayland.session import WaylandLayerShellSessionBackend
from docking.platform.backends.wayland.treeland import (
    TreelandDesktopActionService,
    TreelandVisibilityService,
)

if TYPE_CHECKING:
    from docking.platform.backends.base import ScreenCaptureService
    from docking.platform.backends.wayland.runtime import WaylandProtocolRuntime
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel


class TreelandSessionBackend(WaylandLayerShellSessionBackend):
    """Decorate the generic backend with overlap and Show Desktop support."""

    protocol_profile = TREELAND_PROTOCOL_PROFILE

    def __init__(
        self,
        *,
        layer_shell: object,
        model: DockModel,
        launcher: Launcher,
        protocol_runtime: WaylandProtocolRuntime | None = None,
        screen_capture: ScreenCaptureService | None = None,
    ) -> None:
        super().__init__(
            layer_shell=layer_shell,
            model=model,
            launcher=launcher,
            protocol_runtime=protocol_runtime,
            screen_capture=screen_capture,
        )
        runtime = self._services.protocol_runtime
        overlap = runtime.treeland_overlap_protocol if runtime is not None else None
        window_management = (
            runtime.treeland_window_management_protocol if runtime is not None else None
        )
        visibility = (
            TreelandVisibilityService(adapter=overlap) if overlap is not None else None
        )
        desktop_actions = (
            TreelandDesktopActionService(adapter=window_management)
            if window_management is not None
            else None
        )
        self._services = replace(
            self._services,
            visibility=visibility or self._services.visibility,
            desktop_actions=desktop_actions,
        )

    @property
    def name(self) -> str:
        return "treeland"

    @property
    def capabilities(self) -> PlatformCapabilities:
        base = super().capabilities
        return replace(
            base,
            supports_show_desktop=self._services.desktop_actions is not None,
            supports_overlap_any=isinstance(
                self._services.visibility,
                TreelandVisibilityService,
            ),
        )
