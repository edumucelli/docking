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
from docking.platform.launcher import Launcher
from docking.platform.model import DockModel
from docking.platform.window_tracker import WindowTracker
from docking.ui.autohide import AutoHideController
from docking.ui.dnd import DnDHandler
from docking.ui.dock_window import DockWindow
from docking.ui.menu import MenuHandler
from docking.ui.preview import PreviewPopup
from docking.ui.renderer import DockRenderer
from docking.ui.runtime import DockRuntime


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
    runtime = DockRuntime(window)

    dnd = DnDHandler(
        window=window,
        model=model,
        config=config,
        renderer=renderer,
        theme=theme,
        launcher=launcher,
        geometry_builder=window.geometry,
    )
    menu = MenuHandler(
        parent_window=window,
        runtime=runtime,
        model=model,
        config=config,
        tracker=window_tracker,
        geometry_builder=window.geometry,
        launcher=launcher,
    )
    preview = PreviewPopup(window_tracker)
    window.attach_runtime(autohide=autohide, dnd=dnd, menu=menu, preview=preview)
    return window
