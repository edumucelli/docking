# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""X11 runtime construction helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from docking.platform.backends.base import (
    DisplayServer,
    PlatformCapabilities,
    SessionBackend,
)
from docking.platform.backends.x11.services.actions import WnckDesktopActionService
from docking.platform.backends.x11.services.capture import X11ScreenCaptureService
from docking.platform.backends.x11.services.idle import X11IdleService
from docking.platform.backends.x11.services.picking import WnckWindowPickService
from docking.platform.backends.x11.services.previews import X11PreviewService
from docking.platform.backends.x11.services.surface import X11SurfaceService
from docking.platform.backends.x11.services.visibility import X11VisibilityService
from docking.platform.backends.x11.services.windows import X11WindowService
from docking.platform.backends.x11.services.workspaces import WnckWorkspaceService

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel


@dataclass(frozen=True)
class X11RuntimeServices:
    """Concrete X11 services selected by the X11 session backend."""

    windows: X11WindowService
    previews: X11PreviewService
    surface: X11SurfaceService
    visibility: X11VisibilityService
    workspaces: WnckWorkspaceService
    window_picker: WnckWindowPickService
    idle: X11IdleService
    screen_capture: X11ScreenCaptureService
    desktop_actions: WnckDesktopActionService


class X11SessionBackend(SessionBackend):
    """SessionBackend implementation for the current X11 runtime.

    This backend intentionally remains X11-only. It groups the concrete X11
    services so application startup depends on one session backend shape before
    native Wayland services exist.
    """

    def __init__(self, *, model: DockModel, launcher: Launcher, config: Config) -> None:
        windows = X11WindowService(model=model, launcher=launcher, config=config)
        self._services = X11RuntimeServices(
            windows=windows,
            previews=X11PreviewService(window_tracker=windows),
            surface=X11SurfaceService(),
            visibility=X11VisibilityService(config=config),
            workspaces=WnckWorkspaceService(),
            window_picker=WnckWindowPickService(),
            idle=X11IdleService(),
            screen_capture=X11ScreenCaptureService(),
            desktop_actions=WnckDesktopActionService(),
        )

    @property
    def name(self) -> str:
        return DisplayServer.X11.value

    @property
    def display_server(self) -> DisplayServer:
        return DisplayServer.X11

    @property
    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            tracks_windows=True,
            tracks_active_window=True,
            tracks_attention=True,
            tracks_minimized=True,
            tracks_maximized=True,
            tracks_fullscreen=True,
            tracks_stacking_order=True,
            supports_activate=True,
            supports_minimize=True,
            supports_close=True,
            supports_window_menu=True,
            tracks_window_geometry=True,
            tracks_window_workspace=True,
            supports_current_workspace_filter=True,
            supports_workspace_list=True,
            supports_workspace_switch=True,
            supports_show_desktop=True,
            supports_screen_reservation=True,
            supports_input_region=True,
            supports_pointer_barrier=True,
            supports_background_blur_hint=True,
            supports_overlap_active=True,
            supports_overlap_any=True,
            supports_overlap_maximized=True,
            supports_screen_color_pick=True,
            supports_idle_time=True,
            supports_window_pick=True,
            supports_window_pid=True,
            supports_process_kill=True,
        )

    @property
    def windows(self) -> X11WindowService:
        return self._services.windows

    @property
    def surface(self) -> X11SurfaceService:
        return self._services.surface

    @property
    def visibility(self) -> X11VisibilityService:
        return self._services.visibility

    @property
    def previews(self) -> X11PreviewService:
        return self._services.previews

    @property
    def workspaces(self) -> WnckWorkspaceService:
        return self._services.workspaces

    @property
    def desktop_actions(self) -> WnckDesktopActionService:
        return self._services.desktop_actions

    @property
    def screen_capture(self) -> X11ScreenCaptureService:
        return self._services.screen_capture

    @property
    def idle(self) -> X11IdleService:
        return self._services.idle

    @property
    def window_picker(self) -> WnckWindowPickService:
        return self._services.window_picker

    def start(self) -> None:
        self._services.windows.start()
        self._services.previews.start()
        self._services.surface.start()
        self._services.visibility.start()
        self._services.workspaces.start()
        self._services.window_picker.start()
        self._services.idle.start()
        self._services.screen_capture.start()
        self._services.desktop_actions.start()

    def stop(self) -> None:
        self._services.desktop_actions.stop()
        self._services.screen_capture.stop()
        self._services.idle.stop()
        self._services.window_picker.stop()
        self._services.workspaces.stop()
        self._services.visibility.stop()
        self._services.surface.stop()
        self._services.previews.stop()
        self._services.windows.stop()
