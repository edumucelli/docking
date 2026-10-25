"""Windows session composition; capabilities grow with verified native services."""

from __future__ import annotations

from docking.platform.backends.base import DisplayServer, PlatformCapabilities
from docking.platform.backends.reduced.session import (
    ReducedRuntimeServices,
    ReducedSessionBackend,
)


class WindowsSessionBackend(ReducedSessionBackend):
    """Run the shared GTK dock on Windows with native surface positioning.

    Window tracking and shell services are added separately. Do not advertise
    those capabilities before the corresponding implementation is available.
    """

    def __init__(self) -> None:
        super().__init__()
        from .surface import WindowsSurfaceService

        self._services = ReducedRuntimeServices(
            windows=self._services.windows,
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
        return PlatformCapabilities()
