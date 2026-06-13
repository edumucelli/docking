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

from docking.core.config import Config
from docking.core.theme import Theme
from docking.platform.backends.base import (
    PreviewService,
    Rect,
    SurfaceService,
    VisibilityService,
    WindowService,
)
from docking.platform.launcher import Launcher
from docking.platform.model import DockModel
from docking.ui.display import window_screen_position
from docking.ui.dock_window import DockWindow
from docking.ui.renderer import DockRenderer


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
    session_backend: object | None = None,
) -> DockWindow:
    """Build a fully wired dock window and its UI collaborators."""
    if session_backend is None:
        window = DockWindow(
            config=config,
            model=model,
            renderer=renderer,
            theme=theme,
            window_tracker=window_tracker,
            launcher=launcher,
            preview_service=preview_service,
            surface_service=surface_service,
        )
    else:
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
    return window
