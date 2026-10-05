"""Cinnamon Wayland sessions with layer-shell or built-in shell positioning."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from docking.platform.backends.base import (
    DesktopActionService,
    DisplayServer,
    IdleService,
    PlatformCapabilities,
    PreviewService,
    ScreenCaptureService,
    SessionBackend,
    SurfaceService,
    VisibilityService,
    WindowPickService,
    WindowService,
    WorkspaceService,
)
from docking.platform.backends.cinnamon.applets import (
    CinnamonColorPickerService,
    CinnamonIdleService,
)
from docking.platform.backends.cinnamon.muffin import (
    MuffinDebugClient,
    MuffinWindowService,
)
from docking.platform.backends.cinnamon.picking import CinnamonWindowPickService
from docking.platform.backends.cinnamon.services import (
    CinnamonDesktopActionService,
    CinnamonPreviewService,
    CinnamonVisibilityService,
    CinnamonWorkspaceService,
)
from docking.platform.backends.cinnamon.shell import (
    NATIVE_FEATURES,
    CinnamonShellClient,
    CinnamonShellSurfaceService,
    CinnamonXWaylandSurfaceService,
)
from docking.platform.backends.cinnamon.windows import CinnamonWindowService
from docking.platform.backends.reduced.services import (
    ReducedPreviewService,
    ReducedVisibilityService,
)
from docking.platform.backends.wayland.session import (
    WaylandLayerShellRuntimeServices,
    WaylandLayerShellSessionBackend,
)

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.model import DockModel


@dataclass(frozen=True)
class CinnamonShellRuntimeServices:
    """Native Cinnamon shell services and fallbacks for unsupported features."""

    windows: CinnamonWindowService
    surface: SurfaceService
    previews: PreviewService
    visibility: VisibilityService
    workspaces: WorkspaceService | None
    desktop_actions: DesktopActionService | None
    window_picker: WindowPickService | None
    screen_capture: ScreenCaptureService | None
    idle: IdleService | None


class CinnamonShellSessionBackend(SessionBackend):
    """Shell window management and dock placement without layer-shell."""

    def __init__(
        self,
        *,
        client: CinnamonShellClient,
        config: Config | None = None,
        model: DockModel,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
        surface: SurfaceService | None = None,
    ) -> None:
        windows = CinnamonWindowService(
            model=model,
            client=client,
            config=config,
            application_registry=application_registry,
            process_identity_service=process_identity_service,
        )
        features = getattr(client, "features", NATIVE_FEATURES)
        self._services = CinnamonShellRuntimeServices(
            windows=windows,
            surface=surface or CinnamonShellSurfaceService(client=client),
            previews=CinnamonPreviewService(client=client)
            if "previews" in features
            else ReducedPreviewService(),
            visibility=CinnamonVisibilityService(windows=windows, config=config)
            if "visibility" in features
            else ReducedVisibilityService(),
            workspaces=CinnamonWorkspaceService(windows=windows)
            if "workspaces" in features
            else None,
            desktop_actions=CinnamonDesktopActionService(client=client)
            if "desktop" in features
            else None,
            window_picker=CinnamonWindowPickService(windows=windows)
            if "picking" in features
            else None,
            screen_capture=CinnamonColorPickerService.connect(client=client),
            idle=CinnamonIdleService.connect(),
        )

    @property
    def name(self) -> str:
        return "cinnamon-shell"

    @property
    def display_server(self) -> DisplayServer:
        return DisplayServer.WAYLAND

    @property
    def capabilities(self) -> PlatformCapabilities:
        return _native_capabilities(
            _window_capabilities(
                PlatformCapabilities(supports_screen_reservation=True), actions=True
            ),
            self._services,
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
        return self._services.desktop_actions

    @property
    def screen_capture(self) -> ScreenCaptureService | None:
        return self._services.screen_capture

    @property
    def idle(self) -> IdleService | None:
        return self._services.idle

    @property
    def window_picker(self) -> WindowPickService | None:
        return self._services.window_picker

    def start(self) -> None:
        self._services.windows.start()
        self._services.previews.start()
        self._services.surface.start()
        self._services.visibility.start()
        if self._services.workspaces is not None:
            self._services.workspaces.start()
        if self._services.desktop_actions is not None:
            self._services.desktop_actions.start()
        if self._services.window_picker is not None:
            self._services.window_picker.start()
        if self._services.screen_capture is not None:
            self._services.screen_capture.start()
        if self._services.idle is not None:
            self._services.idle.start()

    def stop(self) -> None:
        if self._services.idle is not None:
            self._services.idle.stop()
        if self._services.screen_capture is not None:
            self._services.screen_capture.stop()
        if self._services.window_picker is not None:
            self._services.window_picker.stop()
        if self._services.desktop_actions is not None:
            self._services.desktop_actions.stop()
        if self._services.workspaces is not None:
            self._services.workspaces.stop()
        self._services.visibility.stop()
        self._services.surface.stop()
        self._services.previews.stop()
        self._services.windows.stop()


class CinnamonXWaylandSessionBackend(CinnamonShellSessionBackend):
    """An X11 dock surface with native Cinnamon window and applet services."""

    def __init__(
        self,
        *,
        client: CinnamonShellClient,
        config: Config | None = None,
        model: DockModel,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
    ) -> None:
        super().__init__(
            client=client,
            config=config,
            model=model,
            application_registry=application_registry,
            process_identity_service=process_identity_service,
            surface=CinnamonXWaylandSurfaceService(client=client),
        )

    @property
    def name(self) -> str:
        return "cinnamon-xwayland"

    @property
    def display_server(self) -> DisplayServer:
        return DisplayServer.X11


@dataclass(frozen=True)
class CinnamonWaylandRuntimeServices(WaylandLayerShellRuntimeServices):
    visibility: VisibilityService
    desktop_actions: DesktopActionService | None = None
    window_picker: WindowPickService | None = None


class CinnamonWaylandSessionBackend(WaylandLayerShellSessionBackend):
    def __init__(
        self,
        *,
        layer_shell: object,
        config: Config | None = None,
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
                config=config,
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
        previous = self._services
        services = CinnamonWaylandRuntimeServices(
            **{
                field: getattr(previous, field)
                for field in previous.__dataclass_fields__
            }
        )
        if shell_client is not None and isinstance(windows, CinnamonWindowService):
            features = shell_client.features
            services = replace(
                services,
                windows=windows,
                previews=CinnamonPreviewService(client=shell_client)
                if "previews" in features
                else ReducedPreviewService(),
                visibility=CinnamonVisibilityService(windows=windows, config=config)
                if "visibility" in features
                else ReducedVisibilityService(),
                workspaces=CinnamonWorkspaceService(windows=windows)
                if "workspaces" in features
                else previous.workspaces,
                desktop_actions=CinnamonDesktopActionService(client=shell_client)
                if "desktop" in features
                else None,
                window_picker=CinnamonWindowPickService(windows=windows)
                if "picking" in features
                else None,
                screen_capture=previous.screen_capture
                or CinnamonColorPickerService.connect(client=shell_client),
                idle=previous.idle or CinnamonIdleService.connect(),
            )
        else:
            services = replace(services, windows=windows)
        self._services = services

    @property
    def name(self) -> str:
        return "cinnamon-wayland"

    @property
    def capabilities(self) -> PlatformCapabilities:
        base = _window_capabilities(
            super().capabilities,
            actions=isinstance(self.windows, CinnamonWindowService),
        )
        return _native_capabilities(base, self._services)

    @property
    def desktop_actions(self) -> DesktopActionService | None:
        return self._services.desktop_actions

    @property
    def window_picker(self) -> WindowPickService | None:
        return self._services.window_picker

    def start(self) -> None:
        super().start()
        if self.desktop_actions is not None:
            self.desktop_actions.start()
        if self.window_picker is not None:
            self.window_picker.start()

    def stop(self) -> None:
        if self.window_picker is not None:
            self.window_picker.stop()
        if self.desktop_actions is not None:
            self.desktop_actions.stop()
        super().stop()


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


def _native_capabilities(base: PlatformCapabilities, services) -> PlatformCapabilities:
    native = isinstance(services.windows, CinnamonWindowService)
    return replace(
        base,
        supports_current_workspace_filter=native
        and isinstance(services.workspaces, CinnamonWorkspaceService),
        supports_workspace_list=services.workspaces is not None,
        supports_workspace_switch=services.workspaces is not None,
        supports_overlap_active=isinstance(
            services.visibility, CinnamonVisibilityService
        ),
        supports_overlap_any=isinstance(services.visibility, CinnamonVisibilityService),
        supports_overlap_maximized=isinstance(
            services.visibility, CinnamonVisibilityService
        ),
        supports_show_desktop=services.desktop_actions is not None,
        supports_screen_color_pick=services.screen_capture is not None,
        supports_idle_time=services.idle is not None,
        supports_window_pick=services.window_picker is not None,
        supports_window_pid=services.window_picker is not None,
        supports_process_kill=services.window_picker is not None,
    )
