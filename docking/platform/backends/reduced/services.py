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

"""Reduced backend services with no taskbar/window-manager powers."""

from __future__ import annotations

from collections.abc import Sequence

from docking.platform.applications.constants import FALLBACK_ICON
from docking.platform.backends.base import (
    ActionResult,
    PlacementRequest,
    PreviewImage,
    PreviewService,
    Rect,
    ReservationRequest,
    SurfaceService,
    VisibilityMonitor,
    VisibilityService,
    WindowId,
    WindowService,
    WindowSnapshot,
)
from docking.platform.backends.diagnostics import WindowTrackingDiagnostic
from docking.platform.backends.gtk_surface import apply_dock_hints, place_window


class ReducedWindowService(WindowService):
    """WindowService for sessions without taskbar/window-management support."""

    def diagnostic_snapshot(self) -> WindowTrackingDiagnostic:
        return WindowTrackingDiagnostic(
            status="unsupported",
            detail="The selected backend does not track running windows.",
        )

    def start(self) -> None:
        """No runtime state is tracked."""

    def stop(self) -> None:
        """No resources are held."""

    def list_all_windows(self) -> Sequence[WindowSnapshot]:
        """Return no windows in reduced mode."""
        return ()

    def list_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        """Return no window rows in reduced mode."""
        return ()

    def list_preview_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        """Return no previewable windows in reduced mode."""
        return ()

    def icon_name_for_desktop(self, desktop_id: str) -> str:
        """Return the generic app icon fallback."""
        return FALLBACK_ICON

    def activate(self, window_id: WindowId) -> ActionResult:
        """Window activation is unavailable."""
        return ActionResult.UNSUPPORTED

    def activate_most_recent(self, desktop_id: str) -> ActionResult:
        """Window activation is unavailable."""
        return ActionResult.UNSUPPORTED

    def cycle(self, desktop_id: str) -> ActionResult:
        """Window cycling is unavailable."""
        return ActionResult.UNSUPPORTED

    def minimize_all(self, desktop_id: str) -> ActionResult:
        """Window minimization is unavailable."""
        return ActionResult.UNSUPPORTED

    def close(self, window_id: WindowId) -> ActionResult:
        """Window closing is unavailable."""
        return ActionResult.UNSUPPORTED

    def close_all(self, desktop_id: str) -> ActionResult:
        """Window closing is unavailable."""
        return ActionResult.UNSUPPORTED

    def close_focused(self, desktop_id: str) -> ActionResult:
        """Window closing is unavailable."""
        return ActionResult.UNSUPPORTED

    def toggle_focus(self, desktop_id: str) -> ActionResult:
        """Window focus toggling is unavailable."""
        return ActionResult.UNSUPPORTED


class ReducedSurfaceService(SurfaceService):
    """Best-effort GTK surface service without compositor-specific features."""

    def __init__(self) -> None:
        self._window: object | None = None

    def start(self) -> None:
        """No runtime state is tracked."""

    def stop(self) -> None:
        """No resources are held."""

    def configure_before_realize(self, window: object) -> None:
        """Apply generic dock-window hints and remember the window."""
        self._window = window
        apply_dock_hints(window)

    def on_realize(self, window: object) -> None:
        """Reapply dock-window hints once the native window exists."""
        self._window = window
        apply_dock_hints(window)

    def set_workspace_scope(self, *, current_workspace_only: bool) -> None:
        """Workspace stickiness is unavailable in the reduced backend."""

    def position_or_anchor(self, request: PlacementRequest) -> None:
        """Apply generic GTK sizing and movement where available."""
        window = self._window
        if window is None:
            return
        place_window(window, request)

    def set_reservation(self, request: ReservationRequest) -> None:
        """Screen reservation is unavailable."""

    def clear_reservation(self) -> None:
        """Screen reservation is unavailable."""

    def update_pointer_barrier(
        self,
        *,
        monitor,
        position: object,
        enabled: bool,
        pressure_callback=None,
        pressure_threshold: int = 1,
    ) -> None:
        """Pointer barriers are unavailable."""

    def update_input_region(self, rect: Rect) -> None:
        """Input shaping is unavailable."""

    def set_blur_region(self, rect: Rect | None) -> None:
        """Blur hints are unavailable."""


class ReducedVisibilityService(VisibilityService):
    """VisibilityService for sessions without foreign-window overlap tracking."""

    def start(self) -> None:
        """No runtime state is tracked."""

    def stop(self) -> None:
        """No resources are held."""

    def create_monitor(self, *, get_dock_rect, on_change) -> VisibilityMonitor | None:
        """Overlap monitoring is unavailable."""
        return None


class ReducedPreviewService(PreviewService):
    """PreviewService for sessions without window capture support."""

    def start(self) -> None:
        """No runtime state is tracked."""

    def stop(self) -> None:
        """No resources are held."""

    def capture(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        """Preview capture is unavailable."""
        return None

    def thumbnail(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        """Menu thumbnail capture is unavailable."""
        return None
