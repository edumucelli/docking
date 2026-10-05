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
from docking.platform.backends.cinnamon.windows import CinnamonWindowService
from docking.platform.backends.reduced.session import ReducedSessionBackend
from docking.platform.backends.wayland.session import WaylandLayerShellSessionBackend

if TYPE_CHECKING:
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.model import DockModel


class CinnamonShellSessionBackend(ReducedSessionBackend):
    """Native dock placement for Cinnamon releases without layer-shell."""

    def __init__(
        self,
        *,
        client: CinnamonShellClient,
        model: DockModel,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
    ) -> None:
        super().__init__()
        self._services = replace(
            self._services,
            surface=CinnamonShellSurfaceService(client=client),
            windows=CinnamonWindowService(
                model=model,
                client=client,
                application_registry=application_registry,
                process_identity_service=process_identity_service,
            ),
        )

    @property
    def name(self) -> str:
        return "cinnamon-shell"

    @property
    def display_server(self) -> DisplayServer:
        return DisplayServer.WAYLAND

    @property
    def capabilities(self) -> PlatformCapabilities:
        return _window_capabilities(
            replace(super().capabilities, supports_screen_reservation=True),
            actions=True,
        )


class CinnamonWaylandSessionBackend(WaylandLayerShellSessionBackend):
    def __init__(
        self,
        *,
        layer_shell: object,
        model,
        client: MuffinDebugClient | None = None,
        shell_client: CinnamonShellClient | None = None,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
    ):
        super().__init__(
            layer_shell=layer_shell,
            model=model,
            application_registry=application_registry,
            process_identity_service=process_identity_service,
        )
        if shell_client is not None:
            windows = CinnamonWindowService(
                model=model,
                client=shell_client,
                application_registry=application_registry,
                process_identity_service=process_identity_service,
            )
        elif client is not None:
            windows = MuffinWindowService(
                model=model,
                application_registry=application_registry,
                process_identity_service=process_identity_service,
                client=client,
            )
        else:
            raise ValueError("Cinnamon window tracking requires a snapshot client")
        self._services = replace(self._services, windows=windows)

    @property
    def name(self) -> str:
        return "cinnamon-wayland"

    @property
    def capabilities(self) -> PlatformCapabilities:
        return _window_capabilities(
            super().capabilities,
            actions=isinstance(self.windows, CinnamonWindowService),
        )


def _window_capabilities(
    base: PlatformCapabilities, *, actions: bool
) -> PlatformCapabilities:
    return replace(
        base,
        tracks_windows=True,
        tracks_active_window=True,
        tracks_attention=True,
        tracks_minimized=actions,
        tracks_maximized=actions,
        tracks_fullscreen=actions,
        tracks_window_geometry=True,
        tracks_window_workspace=True,
        supports_activate=actions,
        supports_minimize=actions,
        supports_close=actions,
        supports_window_menu=True,
    )
