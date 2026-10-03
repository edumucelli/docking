"""Cinnamon Wayland sessions with layer-shell or built-in shell positioning."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from docking.platform.backends.base import DisplayServer, PlatformCapabilities
from docking.platform.backends.cinnamon.muffin import (
    MuffinDebugClient,
    MuffinWindowService,
)
from docking.platform.backends.cinnamon.shell import (
    CinnamonShellClient,
    CinnamonShellSurfaceService,
)
from docking.platform.backends.reduced.session import ReducedSessionBackend
from docking.platform.backends.wayland.session import WaylandLayerShellSessionBackend

if TYPE_CHECKING:
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry


class CinnamonShellSessionBackend(ReducedSessionBackend):
    """Native dock placement for Cinnamon releases without layer-shell."""

    def __init__(self, *, client: CinnamonShellClient) -> None:
        super().__init__()
        self._services = replace(
            self._services, surface=CinnamonShellSurfaceService(client=client)
        )

    @property
    def name(self) -> str:
        return "cinnamon-shell"

    @property
    def display_server(self) -> DisplayServer:
        return DisplayServer.WAYLAND

    @property
    def capabilities(self) -> PlatformCapabilities:
        return replace(super().capabilities, supports_screen_reservation=True)


class CinnamonWaylandSessionBackend(WaylandLayerShellSessionBackend):
    def __init__(
        self,
        *,
        layer_shell: object,
        model,
        client: MuffinDebugClient,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
    ):
        super().__init__(
            layer_shell=layer_shell,
            model=model,
            application_registry=application_registry,
            process_identity_service=process_identity_service,
        )
        self._services = replace(
            self._services,
            windows=MuffinWindowService(
                model=model,
                application_registry=application_registry,
                process_identity_service=process_identity_service,
                client=client,
            ),
        )

    @property
    def name(self) -> str:
        return "cinnamon-wayland"

    @property
    def capabilities(self) -> PlatformCapabilities:
        base = super().capabilities
        return replace(
            base,
            tracks_windows=True,
            tracks_active_window=True,
            tracks_attention=True,
            tracks_minimized=False,
            tracks_maximized=False,
            tracks_fullscreen=False,
            tracks_window_geometry=True,
            tracks_window_workspace=True,
            supports_activate=False,
            supports_minimize=False,
            supports_close=False,
            supports_window_menu=True,
        )
