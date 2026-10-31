"""Windows session composition; capabilities grow with verified native services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from docking.platform.backends.base import (
    DisplayServer,
    PlatformCapabilities,
    SessionBackend,
)

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.model import DockModel

    from .desktop import WindowsDesktopActionService
    from .idle import WindowsIdleService
    from .picking import WindowsWindowPickService
    from .previews import WindowsPreviewService
    from .screen_capture import WindowsScreenCaptureService
    from .surface import WindowsSurfaceService
    from .visibility import WindowsVisibilityService
    from .windows import WindowsWindowService
    from .workspaces import WindowsWorkspaceService


@dataclass(frozen=True)
class WindowsRuntimeServices:
    """Concrete mandatory services selected by the Windows session backend."""

    windows: WindowsWindowService
    previews: WindowsPreviewService
    surface: WindowsSurfaceService
    visibility: WindowsVisibilityService


class WindowsSessionBackend(SessionBackend):
    """Run the shared GTK dock on Windows with native surface positioning.

    Native tracking and taskbar actions share the same immutable window model
    as other backends. AppBar reservation and overlap policy use native monitor
    geometry. Previews use worker-owned Windows graphics capture. Virtual
    desktops use worker-owned shell services on identified Windows builds.
    """

    def __init__(
        self,
        *,
        model: DockModel,
        config: Config,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
    ) -> None:
        from .desktop import WindowsDesktopActionService
        from .idle import WindowsIdleService
        from .picking import WindowsWindowPickService
        from .previews import WindowsPreviewService
        from .screen_capture import WindowsScreenCaptureService
        from .surface import WindowsSurfaceService
        from .visibility import WindowsVisibilityService
        from .windows import WindowsWindowService
        from .workspaces import WindowsWorkspaceService

        self._workspaces = WindowsWorkspaceService()
        windows = WindowsWindowService(
            model=model,
            config=config,
            application_registry=application_registry,
            process_identity_service=process_identity_service,
            workspaces=self._workspaces,
        )
        surface = WindowsSurfaceService(workspaces=self._workspaces)
        self._idle = WindowsIdleService()
        self._screen_capture = WindowsScreenCaptureService()
        self._desktop_actions = WindowsDesktopActionService()
        self._window_picker = WindowsWindowPickService()
        self._services = WindowsRuntimeServices(
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
            supports_workspace_list=self._workspaces.supported,
            supports_workspace_switch=self._workspaces.supported,
            tracks_window_workspace=self._workspaces.supported,
            supports_current_workspace_filter=self._workspaces.supported,
        )

    @property
    def windows(self) -> WindowsWindowService:
        return self._services.windows

    @property
    def surface(self) -> WindowsSurfaceService:
        return self._services.surface

    @property
    def visibility(self) -> WindowsVisibilityService:
        return self._services.visibility

    @property
    def previews(self) -> WindowsPreviewService:
        return self._services.previews

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

    @property
    def workspaces(self) -> WindowsWorkspaceService:
        return self._workspaces

    def start(self) -> None:
        self._workspaces.start()
        self._services.windows.start()
        self._services.previews.start()
        self._services.surface.start()
        self._services.visibility.start()
        self._idle.start()
        self._screen_capture.start()
        self._desktop_actions.start()
        self._window_picker.start()

    def stop(self) -> None:
        self._workspaces.stop()
        self._window_picker.stop()
        self._desktop_actions.stop()
        self._screen_capture.stop()
        self._idle.stop()
        self._services.visibility.stop()
        self._services.surface.stop()
        self._services.previews.stop()
        self._services.windows.stop()
