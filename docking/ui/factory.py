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

from contextlib import ExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING

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
from docking.platform.model import DockModel
from docking.search.controller import GlobalSearchController
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

if TYPE_CHECKING:
    from docking.platform.applications.launcher import ApplicationLauncher
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.icons import IconLoader
    from docking.platform.targets import TargetService


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
    search: GlobalSearchController
    settings: SettingsWindowController

    def start(self) -> None:
        """Start the composed dock UI lifecycle."""
        self.input_controller.start()
        self.search.start()
        self.startup_popups.start()

    def stop(self) -> None:
        """Stop the composed dock UI lifecycle."""
        with ExitStack() as cleanup:
            cleanup.callback(self.input_controller.stop)
            cleanup.callback(self.search.stop)
            cleanup.callback(self.settings.close)
            cleanup.callback(self.startup_popups.stop)


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
    application_registry: ApplicationRegistry,
    session_backend: SessionBackend,
    application_launcher: ApplicationLauncher,
    icon_loader: IconLoader,
    target_service: TargetService,
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
    search = GlobalSearchController(
        config=config,
        model=model,
        windows=window_tracker,
        preview_service=preview_service,
        application_registry=application_registry,
        application_launcher=application_launcher,
        icon_loader=icon_loader,
        target_service=target_service,
    )
    folder_stack = FolderStackController(
        config=config,
        runtime=runtime,
        dock_window=window,
        target_service=target_service,
    )
    dnd = DnDHandler(
        drawing_area=window.drawing_area,
        window=window,
        model=model,
        config=config,
        renderer=renderer,
        theme=theme,
        geometry_builder=window.geometry,
        folder_stack=folder_stack,
        application_registry=application_registry,
        application_launcher=application_launcher,
        icon_loader=icon_loader,
        target_service=target_service,
    )
    settings_actions = SettingsActions(
        runtime=runtime,
        dnd=dnd,
        model=model,
        search=search,
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
        dock_window=window,
        search=search,
        application_registry=application_registry,
        application_launcher=application_launcher,
        target_service=target_service,
    )
    interactions = DockInteractions(
        menu=menu,
        folder_stack=folder_stack,
    )
    input_controller = DockInputController(
        window=window,
        interactions=interactions,
        dnd=dnd,
        application_launcher=application_launcher,
        target_service=target_service,
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
        dock_rect = window.geometry.build_frame().static_dock_rect
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
        search=search,
        settings=settings,
    )
