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

"""Composition root for building a dock window plus edge-dodge integration.

`DockWindow` now leaves construction already owning its UI collaborators. This
module stays as the thin bootstrap layer for the extra platform piece that does
not belong inside the GTK shell itself: the dodge monitor.
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
from docking.ui.renderer import DockRenderer
from docking.ui.runtime import DockRuntime
from docking.ui.settings import SettingsActions, SettingsWindowController
from docking.ui.update_popup import UpdateCheckController


@dataclass(slots=True)
class DockUi:
    """Handle for the composed dock UI graph."""

    window: DockWindow
    update_checker: UpdateCheckController
    input_controller: DockInputController

    def start(self) -> None:
        """Start the composed dock UI lifecycle."""
        self.input_controller.start()
        self.update_checker.start()

    def stop(self) -> None:
        """Stop the composed dock UI lifecycle."""
        self.update_checker.stop()
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
    """Build a fully wired dock window and its UI collaborators."""
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
        update_checker=update_checker,
        input_controller=input_controller,
    )
