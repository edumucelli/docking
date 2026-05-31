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

"""Backend-neutral platform contracts.

These types define the shape Docking's UI and applets should eventually use
instead of depending directly on X11/Wnck/GdkX11 objects. This module must stay
free of GTK, Wnck, GdkX11, Wayland protocol objects, and other concrete desktop
bindings so it can be imported before runtime backend selection.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from docking.platform.model import DockModel
    from docking.platform.running import RunningAppInfo


class DisplayServer(Enum):
    """Display-server family used by the selected session backend."""

    NONE = "none"
    X11 = "x11"
    WAYLAND = "wayland"


class ActionResult(Enum):
    """Result of a backend window/session action."""

    OK = "ok"
    UNSUPPORTED = "unsupported"
    NOT_FOUND = "not_found"
    FAILED = "failed"

    @property
    def succeeded(self) -> bool:
        """Return True only when the backend accepted and performed the action."""
        return self is ActionResult.OK


@dataclass(frozen=True)
class Rect:
    """Backend-neutral rectangle in logical screen or surface coordinates."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def overlaps(self, other: Rect) -> bool:
        """Return True when this rectangle intersects another rectangle."""
        return (
            self.x < other.right
            and self.right > other.x
            and self.y < other.bottom
            and self.bottom > other.y
        )


@dataclass(frozen=True)
class Size:
    """Backend-neutral logical size."""

    width: int
    height: int


@dataclass(frozen=True)
class MonitorSnapshot:
    """Stable monitor facts needed by surface backends."""

    index: int
    geometry: Rect
    workarea: Rect | None = None
    scale: int = 1
    primary: bool = False
    name: str | None = None


@dataclass(frozen=True)
class WindowId:
    """Stable backend-owned window identifier.

    X11 implementations may use the XID as ``value`` during migration. Native
    Wayland implementations should use an internal ID that maps back to a live
    protocol handle inside the backend.
    """

    backend: str
    value: str | int

    @classmethod
    def x11(cls, xid: int) -> WindowId:
        """Create an X11 window ID from an XID."""
        return cls(backend="x11", value=int(xid))

    def __str__(self) -> str:
        return f"{self.backend}:{self.value}"


@dataclass(frozen=True)
class WindowSnapshot:
    """Immutable window state exposed to UI code."""

    id: WindowId
    desktop_id: str
    title: str = "Window"
    app_id: str | None = None
    wm_class: str | None = None
    active: bool = False
    urgent: bool = False
    minimized: bool | None = None
    maximized: bool | None = None
    fullscreen: bool | None = None
    geometry: Rect | None = None
    workspace_id: str | None = None
    can_activate: bool = False
    can_minimize: bool = False
    can_close: bool = False
    can_preview: bool = False


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Workspace facts exposed to applets and workspace-aware filters."""

    id: str
    number: int
    name: str = ""
    active: bool = False


@dataclass(frozen=True)
class PreviewImage:
    """Result of a preview capture operation."""

    image: object
    width: int
    height: int


@dataclass(frozen=True)
class PlacementRequest:
    """Request to place or anchor the dock surface."""

    monitor: MonitorSnapshot
    position: object
    size: Size
    gap: int = 0
    keep_above: bool = True


@dataclass(frozen=True)
class ReservationRequest:
    """Request to reserve edge space for the dock."""

    monitor: MonitorSnapshot
    position: object
    thickness: int


@dataclass(frozen=True)
class PlatformCapabilities:
    """Fine-grained platform capabilities for UI and applet decisions."""

    tracks_windows: bool = False
    tracks_active_window: bool = False
    tracks_attention: bool = False
    tracks_minimized: bool = False
    tracks_maximized: bool = False
    tracks_fullscreen: bool = False
    tracks_stacking_order: bool = False
    supports_activate: bool = False
    supports_minimize: bool = False
    supports_close: bool = False
    supports_window_menu: bool = False
    tracks_window_geometry: bool = False
    tracks_window_workspace: bool = False
    supports_current_workspace_filter: bool = False
    supports_workspace_list: bool = False
    supports_workspace_switch: bool = False
    supports_show_desktop: bool = False
    supports_layer_shell: bool = False
    supports_screen_reservation: bool = False
    supports_input_region: bool = False
    supports_pointer_barrier: bool = False
    supports_background_blur_hint: bool = False
    supports_overlap_active: bool = False
    supports_overlap_any: bool = False
    supports_overlap_maximized: bool = False
    supports_screen_color_pick: bool = False
    supports_screenshot: bool = False
    supports_idle_time: bool = False
    supports_window_pick: bool = False
    supports_window_pid: bool = False
    supports_process_kill: bool = False

    @property
    def supports_any_overlap(self) -> bool:
        """Return True when any foreign-window overlap mode is available."""
        return (
            self.supports_overlap_active
            or self.supports_overlap_any
            or self.supports_overlap_maximized
        )


class Service(Protocol):
    """Common lifecycle for optional backend services."""

    def start(self) -> None:
        """Start watching backend state, if the service needs a runtime loop."""

    def stop(self) -> None:
        """Release service resources."""


class WindowService(Service, Protocol):
    """Taskbar/window state and window actions."""

    def bind_model(self, model: DockModel) -> None:
        """Attach the DockModel that receives running-state updates."""

    def snapshot_running(self) -> Mapping[str, RunningAppInfo]:
        """Return latest running-app aggregates."""

    def list_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        """Return current windows for a desktop ID."""

    def activate(self, window_id: WindowId) -> ActionResult:
        """Activate one window by backend ID."""

    def activate_most_recent(self, desktop_id: str) -> ActionResult:
        """Activate the most recent window for an app."""

    def cycle(self, desktop_id: str) -> ActionResult:
        """Cycle windows for an app according to backend policy."""

    def minimize_all(self, desktop_id: str) -> ActionResult:
        """Minimize all windows for an app, when supported."""

    def close(self, window_id: WindowId) -> ActionResult:
        """Close one window by backend ID."""

    def close_all(self, desktop_id: str) -> ActionResult:
        """Close all windows for an app."""


class SurfaceService(Service, Protocol):
    """Dock surface role, screen reservation, and platform edge integration."""

    def configure_before_realize(self, window: object) -> None:
        """Configure platform surface role before the GTK window is realized."""

    def on_realize(self, window: object) -> None:
        """Finish surface setup after realization."""

    def position_or_anchor(self, request: PlacementRequest) -> None:
        """Move an X11 window or configure native Wayland anchors."""

    def set_reservation(self, request: ReservationRequest) -> None:
        """Reserve edge space for an always-visible dock."""

    def clear_reservation(self) -> None:
        """Clear any edge-space reservation owned by this backend."""

    def update_input_region(self, rect: Rect) -> None:
        """Update the interactive region for the dock surface, if supported."""

    def set_blur_region(self, rect: Rect | None) -> None:
        """Set or clear a compositor blur hint, if supported."""


class VisibilityMonitor(Protocol):
    """Runtime monitor for overlap-driven hide modes."""

    def start(self) -> None:
        """Start monitoring foreign-window visibility."""

    def stop(self) -> None:
        """Stop monitoring foreign-window visibility."""

    def evaluate_now(self) -> None:
        """Force immediate reevaluation."""


class VisibilityService(Service, Protocol):
    """Factory for overlap/dodge monitors."""

    def supports_hide_mode(self, mode: object) -> bool:
        """Return whether the selected backend can support a hide mode."""

    def create_monitor(
        self,
        *,
        get_dock_rect: Callable[[], Rect | None],
        on_change: Callable[[bool], None],
    ) -> VisibilityMonitor | None:
        """Create a monitor, or None when overlap tracking is unsupported."""


class PreviewService(Service, Protocol):
    """Window-preview capture and fallback information."""

    def capture(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        """Capture a preview image for one window, if available."""

    def fallback_icon_name(self, window_id: WindowId) -> str | None:
        """Return a fallback icon name for one window."""


class WorkspaceService(Service, Protocol):
    """Workspace list and switching operations."""

    def list_workspaces(self) -> Sequence[WorkspaceSnapshot]:
        """Return known workspaces."""

    def active_workspace(self) -> WorkspaceSnapshot | None:
        """Return the active workspace, if known."""

    def activate(self, workspace_id: str) -> ActionResult:
        """Activate a workspace."""


class DesktopActionService(Service, Protocol):
    """Desktop-wide window manager actions."""

    def show_desktop(self, show: bool | None = None) -> ActionResult:
        """Show, hide, or toggle desktop visibility."""


class ScreenCaptureService(Service, Protocol):
    """Screen capture and color-picking operations."""

    def pick_color(self, *, x: int, y: int) -> tuple[int, int, int] | None:
        """Pick a screen color in RGB byte values."""

    def screenshot(self) -> ActionResult:
        """Request a screenshot through the backend."""


class IdleService(Service, Protocol):
    """Desktop idle-time source."""

    def idle_seconds(self) -> float | None:
        """Return current idle time in seconds, if available."""


class WindowPickService(Service, Protocol):
    """Window picking and process actions used by window-management applets."""

    def pick_window(self) -> WindowSnapshot | None:
        """Ask the user to select a window, if supported."""

    def pid_for(self, window_id: WindowId) -> int | None:
        """Return the process ID for a window, if known."""

    def kill(self, window_id: WindowId) -> ActionResult:
        """Terminate the process or window represented by the backend ID."""


class SessionBackend(Protocol):
    """Selected platform backend for the current desktop session."""

    @property
    def name(self) -> str:
        """Human-readable backend name for logging and diagnostics."""

    @property
    def display_server(self) -> DisplayServer:
        """Display-server family used by this backend."""

    @property
    def capabilities(self) -> PlatformCapabilities:
        """Capabilities available in the selected runtime."""

    @property
    def windows(self) -> WindowService:
        """Window/taskbar service."""

    @property
    def surface(self) -> SurfaceService:
        """Dock surface service."""

    @property
    def visibility(self) -> VisibilityService:
        """Overlap/dodge visibility service."""

    @property
    def previews(self) -> PreviewService:
        """Window preview service."""

    @property
    def workspaces(self) -> WorkspaceService | None:
        """Workspace service, when available."""

    @property
    def desktop_actions(self) -> DesktopActionService | None:
        """Desktop action service, when available."""

    @property
    def screen_capture(self) -> ScreenCaptureService | None:
        """Screen capture service, when available."""

    @property
    def idle(self) -> IdleService | None:
        """Idle-time service, when available."""

    @property
    def window_picker(self) -> WindowPickService | None:
        """Window-picking service, when available."""

    def start(self) -> None:
        """Start all backend services needed for runtime operation."""

    def stop(self) -> None:
        """Stop all backend services."""
