"""Evaluate overlap in the active dock monitor's GTK coordinate space."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from docking.platform.backends.visibility import SnapshotVisibilityService

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.backends.base import Rect, WindowService, WindowSnapshot
    from docking.platform.backends.windows.surface import WindowsSurfaceService


class WindowsVisibilityService(SnapshotVisibilityService):
    def __init__(
        self, *, windows: WindowService, surface: WindowsSurfaceService, config: Config
    ) -> None:
        self._surface = surface
        super().__init__(windows=windows, visible_windows=self._project, config=config)

    def _project(self, _dock: Rect) -> tuple[WindowSnapshot, ...]:
        return tuple(
            replace(window, geometry=self._surface.window_geometry(window.geometry))
            for window in self._windows.list_all_windows()
            if window.geometry is not None
        )
