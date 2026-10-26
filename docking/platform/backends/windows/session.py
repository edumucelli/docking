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

    from .desktop import WindowsDesktopActionService
    from .idle import WindowsIdleService
    from .picking import WindowsWindowPickService
    from .screen_capture import WindowsScreenCaptureService


class WindowsSessionBackend(ReducedSessionBackend):
    """Run the shared GTK dock on Windows with native surface positioning.

    Native tracking and taskbar actions share the same immutable window model
    as other backends. AppBar reservation and overlap policy use native monitor
    geometry. Previews use worker-owned Windows graphics capture. Workspace
    services follow as their native implementation is verified.
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
        from .desktop import WindowsDesktopActionService
        from .idle import WindowsIdleService
        from .picking import WindowsWindowPickService
        from .previews import WindowsPreviewService
        from .screen_capture import WindowsScreenCaptureService
        from .surface import WindowsSurfaceService
        from .visibility import WindowsVisibilityService
        from .windows import WindowsWindowService

        windows = WindowsWindowService(
            model=model,
            config=config,
            application_registry=application_registry,
            process_identity_service=process_identity_service,
        )
        surface = WindowsSurfaceService()
        self._idle = WindowsIdleService()
        self._screen_capture = WindowsScreenCaptureService()
        self._desktop_actions = WindowsDesktopActionService()
        self._window_picker = WindowsWindowPickService()
        self._services = ReducedRuntimeServices(
            windows=windows,
            previews=WindowsPreviewService(
                windows=windows, availability_changed=windows.set_previews_available
            ),
            surface=surface,
            visibility=WindowsVisibilityService(
                windows=windows, surface=surface, config=config
            ),
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
            tracks_maximized=True,
            tracks_fullscreen=True,
            supports_activate=True,
            supports_minimize=True,
            supports_close=True,
            supports_window_pid=True,
            supports_screen_reservation=True,
            supports_overlap_active=True,
            supports_overlap_any=True,
            supports_overlap_maximized=True,
            supports_idle_time=True,
            supports_screen_color_pick=True,
            supports_show_desktop=True,
            supports_window_pick=True,
            supports_process_kill=True,
        )

    @property
    def idle(self) -> WindowsIdleService:
        return self._idle

    @property
    def screen_capture(self) -> WindowsScreenCaptureService:
        return self._screen_capture

    @property
    def desktop_actions(self) -> WindowsDesktopActionService:
        return self._desktop_actions

    @property
    def window_picker(self) -> WindowsWindowPickService:
        return self._window_picker

    def start(self) -> None:
        super().start()
        self._idle.start()
        self._screen_capture.start()
        self._desktop_actions.start()
        self._window_picker.start()

    def stop(self) -> None:
        self._window_picker.stop()
        self._desktop_actions.stop()
        self._screen_capture.stop()
        self._idle.stop()
        super().stop()
