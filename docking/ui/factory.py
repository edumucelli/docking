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
from docking.platform.dodge import ScreenRect, WindowDodgeMonitor
from docking.platform.launcher import Launcher
from docking.platform.model import DockModel
from docking.platform.window_tracker import WindowTracker
from docking.ui.dock_window import DockWindow
from docking.ui.renderer import DockRenderer


def build_dock_window(
    *,
    config: Config,
    model: DockModel,
    renderer: DockRenderer,
    theme: Theme,
    window_tracker: WindowTracker,
    launcher: Launcher,
) -> DockWindow:
    """Build a fully wired dock window and its UI collaborators."""
    window = DockWindow(
        config=config,
        model=model,
        renderer=renderer,
        theme=theme,
        window_tracker=window_tracker,
        launcher=launcher,
    )

    def _get_dock_rect() -> ScreenRect | None:
        if not window.get_realized():
            return None
        wx, wy = window.get_position()
        ww, wh = window.get_size()
        return ScreenRect(x=wx, y=wy, width=ww, height=wh)

    dodge_monitor = WindowDodgeMonitor(
        config=config,
        get_dock_rect=_get_dock_rect,
        on_change=window.autohide.set_window_should_hide,
    )
    window.dodge_monitor = dodge_monitor
    dodge_monitor.start()
    return window
