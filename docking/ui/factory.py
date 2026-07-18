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

"""Composition root for wiring the dock UI object graph.

This module is the hand-off point between process startup (`app.py`) and the
interactive GTK shell. It deliberately owns the wiring that crosses UI
subsystem boundaries:

* `DockWindow` owns the drawing area and low-level shell services.
* `DockRuntime` exposes a narrow command surface for broad controllers such as
  settings and menus, so they do not reach into raw `DockWindow` internals.
* `MenuHandler`, `SettingsWindowController`, drag-and-drop, folder stacks, and
  input handling are composed here because they depend on each other but should
  not construct each other.
* Startup popup sources are registered with one coordinator here so New Year,
  update, and tip popups can share priority rules without knowing about each
  other.

Keeping that graph in one place makes later refactors easier: `app.py` remains
process bootstrap, `DockWindow` remains the dock surface, and feature
controllers receive the smallest collaborator set they need.
"""

from __future__ import annotations

from dataclasses import dataclass

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
from docking.ui.dnd import DnDHandler
from docking.ui.dock_interactions import DockInteractions
from docking.ui.dock_window import DockWindow
from docking.ui.folder.stack import FolderStackController
from docking.ui.input_controller import DockInputController
from docking.ui.menu import MenuHandler
from docking.ui.new_year import NewYearGreetingController
from docking.ui.renderer import DockRenderer
from docking.ui.runtime import DockRuntime
from docking.ui.settings import SettingsActions, SettingsWindowController
from docking.ui.startup_popups import StartupPopupCoordinator
from docking.ui.startup_tips import StartupTipsController
from docking.ui.update_popup import UpdateCheckController


@dataclass(slots=True)
class DockUi:
    """Lifecycle handle for UI pieces that live beside `DockWindow`.

    `DockWindow` is still the GTK window callers need for show/present calls,
    but it intentionally does not own every controller anymore. This wrapper
    gives `app.py` a single start/stop surface for the controllers assembled in
    this module.
    """

    window: DockWindow
    startup_popups: StartupPopupCoordinator
    input_controller: DockInputController

    def start(self) -> None:
        """Start the composed dock UI lifecycle."""
        self.input_controller.start()
        self.startup_popups.start()

    def stop(self) -> None:
        """Stop the composed dock UI lifecycle."""
        self.startup_popups.stop()
        self.input_controller.stop()


def build_dock_window(
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
    """Build a fully wired dock window and its UI collaborators.

    The function name is kept for existing callers/tests, but the returned
    object is the composed dock UI handle rather than a bare `DockWindow`.
    """
    window = DockWindow(
        config=config,
        model=model,
        renderer=renderer,
        theme=theme,
        window_tracker=window_tracker,
        preview_service=preview_service,
        surface_service=surface_service,
        session_backend=session_backend,
    )
    update_checker = UpdateCheckController(window=window, config=config)
    runtime = DockRuntime(window, update_checker=update_checker)
    about = AboutDialogController(parent=window)
    diagnostics = DiagnosticsDialogController(
        parent=window,
        backend=session_backend,
    )
    folder_stack = FolderStackController(
        config=config,
        runtime=runtime,
        launcher=launcher,
        dock_window=window,
    )
    dnd = DnDHandler(
        drawing_area=window.drawing_area,
        window=window,
        model=model,
        config=config,
        renderer=renderer,
        theme=theme,
        launcher=launcher,
        geometry_builder=window.geometry,
        folder_stack=folder_stack,
    )
    settings_actions = SettingsActions(
        runtime=runtime,
        dnd=dnd,
        model=model,
    )
    settings = SettingsWindowController(
        parent=window,
        actions=settings_actions,
        model=model,
        config=config,
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
        folder_stack=folder_stack,
        launcher=launcher,
        dock_window=window,
    )
    interactions = DockInteractions(
        menu=menu,
        folder_stack=folder_stack,
    )
    input_controller = DockInputController(
        window=window,
        interactions=interactions,
        dnd=dnd,
    )
    startup_popups = StartupPopupCoordinator()
    new_year = NewYearGreetingController(window=window)
    startup_tips = StartupTipsController(
        window=window,
        config=config,
    )
    startup_popups.register(new_year)
    startup_popups.register(update_checker)
    startup_popups.register(startup_tips)

    def _get_dock_rect() -> Rect | None:
        # Dodge monitors are backend-owned, but the visible shelf rectangle is
        # a renderer/geometry concern. Query it lazily so monitor callbacks
        # always see the current theme, position, zoom, and scale state.
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
        startup_popups=startup_popups,
        input_controller=input_controller,
    )
