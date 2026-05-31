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

import os
from typing import TYPE_CHECKING

from docking.log import get_logger
from docking.platform.backends.base import (
    DisplayServer,
    PlacementRequest,
    PlatformCapabilities,
    Rect,
    ReservationRequest,
)
from docking.platform.backends.x11.previews import X11PreviewService
from docking.platform.backends.x11.visibility import X11VisibilityService
from docking.platform.backends.x11.windows import X11WindowService
from docking.platform.window_tracker import WindowTracker

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel


X11_WINDOW_SERVICE_ENV = "DOCKING_X11_WINDOW_SERVICE"
X11_WINDOW_SERVICE_LEGACY = "legacy"
X11_WINDOW_SERVICE_SERVICE = "service"

log = get_logger(name="x11_session")


def build_x11_window_tracker(
    *, model: DockModel, launcher: Launcher, config: Config | None = None
) -> WindowTracker:
    """Build the X11 window runtime used by current UI compatibility callers."""
    mode = _window_service_mode()
    if mode == X11_WINDOW_SERVICE_LEGACY:
        return WindowTracker(model=model, launcher=launcher, config=config)
    return X11WindowService(model=model, launcher=launcher, config=config)


def _window_service_mode() -> str:
    raw_mode = os.environ.get(X11_WINDOW_SERVICE_ENV, X11_WINDOW_SERVICE_SERVICE)
    mode = raw_mode.strip().lower()
    if mode in {X11_WINDOW_SERVICE_LEGACY, X11_WINDOW_SERVICE_SERVICE}:
        return mode
    log.warning(
        "Ignoring invalid %s=%r; using %s",
        X11_WINDOW_SERVICE_ENV,
        raw_mode,
        X11_WINDOW_SERVICE_SERVICE,
    )
    return X11_WINDOW_SERVICE_SERVICE


def build_x11_session_backend(
    *, model: DockModel, launcher: Launcher, config: Config | None = None
) -> X11SessionBackend:
    """Build the X11-only session backend used by production startup."""
    return X11SessionBackend(model=model, launcher=launcher, config=config)


class X11SessionBackend:
    """SessionBackend implementation for the current X11 runtime.

    This backend intentionally remains X11-only. It groups the already-migrated
    window, preview, and visibility services so application startup can depend
    on a session backend shape before native Wayland services exist. Surface
    stays transitional until its dedicated migration PR.
    """

    def __init__(
        self, *, model: DockModel, launcher: Launcher, config: Config | None = None
    ) -> None:
        self._windows = build_x11_window_tracker(
            model=model, launcher=launcher, config=config
        )
        self._previews = X11PreviewService(window_tracker=self._windows)
        self._surface = _TransitionalSurfaceService()
        self._visibility = X11VisibilityService(config=config)

    @property
    def name(self) -> str:
        return "x11"

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
            supports_screenshot=True,
            supports_idle_time=True,
            supports_window_pick=True,
            supports_window_pid=True,
            supports_process_kill=True,
        )

    @property
    def windows(self) -> WindowTracker:
        return self._windows

    @property
    def surface(self) -> _TransitionalSurfaceService:
        return self._surface

    @property
    def visibility(self) -> X11VisibilityService:
        return self._visibility

    @property
    def previews(self) -> X11PreviewService:
        return self._previews

    @property
    def workspaces(self) -> None:
        return None

    @property
    def desktop_actions(self) -> None:
        return None

    @property
    def screen_capture(self) -> None:
        return None

    @property
    def idle(self) -> None:
        return None

    @property
    def window_picker(self) -> None:
        return None

    def start(self) -> None:
        self._call_service_method(self._windows, "start")
        self._previews.start()
        self._surface.start()
        self._visibility.start()

    def stop(self) -> None:
        self._visibility.stop()
        self._surface.stop()
        self._previews.stop()
        self._call_service_method(self._windows, "stop")

    @staticmethod
    def _call_service_method(service: object, method_name: str) -> None:
        method = getattr(service, method_name, None)
        if callable(method):
            method()


class _TransitionalSurfaceService:
    """No-op surface service until X11 surface ownership moves in PR 9."""

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def configure_before_realize(self, window: object) -> None:
        return

    def on_realize(self, window: object) -> None:
        return

    def position_or_anchor(self, request: PlacementRequest) -> None:
        return

    def set_reservation(self, request: ReservationRequest) -> None:
        return

    def clear_reservation(self) -> None:
        return

    def update_input_region(self, rect: Rect) -> None:
        return

    def set_blur_region(self, rect: Rect | None) -> None:
        return
