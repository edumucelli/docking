"""Composition root for building a fully assembled dock window.

This module owns UI graph assembly. The dock has one unavoidable construction
cycle:

    DockWindow must exist
      before
    DnD / menu / preview collaborators that depend on that live window
      can be created

Trying to force all of that through ``DockWindow.__init__`` would only hide the
cycle behind placeholders or partially initialized collaborators.

So the assembly model is explicit:

1. create the window shell,
2. create the collaborators that require that shell,
3. attach them in one atomic step,
4. return a fully usable window.

That keeps ``app.py`` small and stops the rest of the runtime from seeing a
half-assembled window with several nullable collaborators.
"""

from __future__ import annotations

from docking.core.config import Config
from docking.core.theme import Theme
from docking.platform.dodge import ScreenRect, WindowDodgeMonitor
from docking.platform.launcher import Launcher
from docking.platform.model import DockModel
from docking.platform.window_tracker import WindowTracker
from docking.ui.about import AboutDialogController
from docking.ui.autohide import AutoHideController
from docking.ui.dnd import DnDHandler
from docking.ui.dock_window import DockComponents, DockWindow
from docking.ui.menu import MenuHandler
from docking.ui.preview import PreviewPopup
from docking.ui.renderer import DockRenderer
from docking.ui.runtime import DockDragRuntime, DockRuntime
from docking.ui.settings import SettingsWindowController


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
    )

    autohide = AutoHideController(window, config)

    def _get_dock_rect() -> ScreenRect | None:
        if not window.get_realized():
            return None
        wx, wy = window.get_position()
        ww, wh = window.get_size()
        return ScreenRect(x=wx, y=wy, width=ww, height=wh)

    dodge_monitor = WindowDodgeMonitor(
        config=config,
        get_dock_rect=_get_dock_rect,
        on_change=autohide.set_window_should_hide,
    )
    dodge_monitor.start()

    runtime = DockRuntime(window)
    drag_runtime = DockDragRuntime(window)
    about = AboutDialogController(parent=window)
    settings = SettingsWindowController(
        parent=window,
        runtime=runtime,
        model=model,
        config=config,
    )

    dnd = DnDHandler(
        drawing_area=window.drawing_area,
        runtime=drag_runtime,
        model=model,
        config=config,
        renderer=renderer,
        theme=theme,
        launcher=launcher,
        geometry_builder=window.geometry,
    )
    menu = MenuHandler(
        about=about,
        settings=settings,
        runtime=runtime,
        model=model,
        config=config,
        window_tracker=window_tracker,
        geometry_builder=window.geometry,
        launcher=launcher,
    )
    preview = PreviewPopup(window_tracker=window_tracker)
    window.attach_components(
        DockComponents(autohide=autohide, dnd=dnd, menu=menu, preview=preview)
    )
    return window
