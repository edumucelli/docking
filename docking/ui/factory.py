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

"""Composition root for building the dock UI graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from docking.core.config import Config
from docking.core.theme import Theme
from docking.platform.backends.base import (
    PreviewService,
    Rect,
    SessionBackend,
    SurfaceService,
    VisibilityService,
    WindowService,
)
from docking.platform.launcher import Launcher
from docking.platform.model import DockModel
from docking.ui.about import AboutDialogController
from docking.ui.diagnostics import DiagnosticsDialogController
from docking.ui.display import window_screen_position
from docking.ui.dock_window import DockWindow
from docking.ui.menu import MenuHandler
from docking.ui.new_year import NewYearGreetingController
from docking.ui.renderer import DockRenderer
from docking.ui.settings import SettingsWindowController
from docking.ui.update_popup import UpdateCheckController


class StartupLifecycle(Protocol):
    """Controller with startup UI lifecycle hooks."""

    def start(self) -> None:
        """Start the controller."""

    def stop(self) -> None:
        """Stop the controller."""


@dataclass(slots=True)
class DockUi:
    """Small UI graph handle returned to the application bootstrap."""

    window: DockWindow
    _startup_controllers: tuple[StartupLifecycle, ...]

    def start_startup_ui(self) -> None:
        """Start startup UI controllers in their configured order."""
        for controller in self._startup_controllers:
            controller.start()

    def stop_startup_ui(self) -> None:
        """Stop startup UI controllers in reverse order."""
        for controller in reversed(self._startup_controllers):
            controller.stop()


def build_dock_ui(
    *,
    config: Config,
    model: DockModel,
    renderer: DockRenderer,
    theme: Theme,
    window_tracker: WindowService,
    preview_service: PreviewService,
    surface_service: SurfaceService,
    visibility_service: VisibilityService,
    launcher: Launcher,
    session_backend: SessionBackend,
) -> DockUi:
    """Build a fully wired dock UI graph."""
    window = DockWindow(
        config=config,
        model=model,
        renderer=renderer,
        theme=theme,
        window_tracker=window_tracker,
        launcher=launcher,
        preview_service=preview_service,
        surface_service=surface_service,
        session_backend=session_backend,
    )
    runtime = window.runtime
    update_checker = UpdateCheckController(
        config=config,
        anchor_provider=window,
    )
    about = AboutDialogController(parent=window)
    diagnostics = DiagnosticsDialogController(
        parent=window,
        backend=session_backend,
    )
    settings = SettingsWindowController(
        parent=window,
        runtime=runtime,
        model=model,
        config=config,
        updates=update_checker,
    )
    menu = MenuHandler(
        about=about,
        settings=settings,
        diagnostics=diagnostics,
        runtime=runtime,
        model=model,
        config=config,
        window_tracker=window_tracker,
        preview_service=preview_service,
        geometry_builder=window.geometry,
        launcher=launcher,
        dock_window=window,
    )
    window.set_menu_handler(menu)
    new_year = NewYearGreetingController(anchor_provider=window)

    def _get_dock_rect() -> Rect | None:
        if not window.get_realized():
            return None
        window_pos = window_screen_position(window)
        wx, wy = window_pos.x, window_pos.y
        dock_rect = window.geometry.build_frame().background_rect
        return Rect(
            x=wx + dock_rect.x,
            y=wy + dock_rect.y,
            width=dock_rect.w,
            height=dock_rect.h,
        )

    dodge_monitor = visibility_service.create_monitor(
        get_dock_rect=_get_dock_rect,
        on_change=window.autohide.set_window_should_hide,
    )
    window.dodge_monitor = dodge_monitor
    if dodge_monitor is not None:
        dodge_monitor.start()
    return DockUi(
        window=window,
        _startup_controllers=(new_year, update_checker),
    )
