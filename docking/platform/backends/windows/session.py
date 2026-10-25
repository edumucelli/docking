"""Windows session composition; capabilities grow with verified native services."""

from __future__ import annotations

from typing import TYPE_CHECKING

from docking.platform.backends.base import DisplayServer, PlatformCapabilities
from docking.platform.backends.reduced.session import (
    ReducedRuntimeServices,
    ReducedSessionBackend,
)

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.model import DockModel


class WindowsSessionBackend(ReducedSessionBackend):
    """Run the shared GTK dock on Windows with native surface positioning.

    Native tracking and taskbar actions share the same immutable window model
    as other backends. Reservation, workspace and preview services follow as
    their native implementations are verified.
    """

    def __init__(
        self,
        *,
        model: DockModel,
        config: Config,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
    ) -> None:
        super().__init__()
        from .surface import WindowsSurfaceService
        from .windows import WindowsWindowService

        self._services = ReducedRuntimeServices(
            windows=WindowsWindowService(
                model=model,
                config=config,
                application_registry=application_registry,
                process_identity_service=process_identity_service,
            ),
            previews=self._services.previews,
            surface=WindowsSurfaceService(),
            visibility=self._services.visibility,
        )

    @property
    def name(self) -> str:
        return "windows"

    @property
    def display_server(self) -> DisplayServer:
        return DisplayServer.WINDOWS

    @property
    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            tracks_windows=True,
            tracks_active_window=True,
            tracks_minimized=True,
            supports_activate=True,
            supports_minimize=True,
            supports_close=True,
            supports_window_pid=True,
        )
