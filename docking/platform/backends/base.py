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

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum


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
    connector: str | None = None


@dataclass(frozen=True)
class WindowId:
    """Stable backend-owned window identifier.

    X11 implementations use the XID as ``value``. Native Wayland implementations
    should use an internal ID that maps back to a live protocol handle inside
    the backend.
    """

    backend: DisplayServer
    value: str | int

    @classmethod
    def x11(cls, xid: int) -> WindowId:
        """Create an X11 window ID from an XID."""
        return cls(backend=DisplayServer.X11, value=int(xid))

    def __str__(self) -> str:
        return f"{self.backend.value}:{self.value}"


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
    x: int
    y: int
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


class Service(ABC):
    """Common lifecycle for optional backend services."""

    @abstractmethod
    def start(self) -> None:
        """Start watching backend state, if the service needs a runtime loop."""

    @abstractmethod
    def stop(self) -> None:
        """Release service resources."""


class WindowService(Service):
    """Taskbar/window state and window actions."""

    @abstractmethod
    def list_all_windows(self) -> Sequence[WindowSnapshot]:
        """Return every current window known to this backend."""

    @abstractmethod
    def list_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        """Return current windows for a desktop ID."""

    @abstractmethod
    def list_preview_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        """Return windows that should appear in preview UI for a desktop ID."""

    @abstractmethod
    def icon_name_for_desktop(self, desktop_id: str) -> str:
        """Return an icon name fallback for a desktop ID."""

    @abstractmethod
    def activate(self, window_id: WindowId) -> ActionResult:
        """Activate one window by backend ID."""

    @abstractmethod
    def activate_most_recent(self, desktop_id: str) -> ActionResult:
        """Activate the most recent window for an app."""

    @abstractmethod
    def cycle(self, desktop_id: str) -> ActionResult:
        """Cycle windows for an app according to backend policy."""

    @abstractmethod
    def minimize_all(self, desktop_id: str) -> ActionResult:
        """Minimize all windows for an app, when supported."""

    @abstractmethod
    def close(self, window_id: WindowId) -> ActionResult:
        """Close one window by backend ID."""

    @abstractmethod
    def close_all(self, desktop_id: str) -> ActionResult:
        """Close all windows for an app."""

    @abstractmethod
    def close_focused(self, desktop_id: str) -> ActionResult:
        """Close the active window if it belongs to an app."""

    @abstractmethod
    def toggle_focus(self, desktop_id: str) -> ActionResult:
        """Toggle focus/minimize behavior for an app."""


class SurfaceService(Service):
    """Dock surface role, screen reservation, and platform edge integration."""

    # TODO: Define a small SurfaceHost/SurfaceWindow ABC for the window
    # methods/properties surface services need, instead of accepting an untyped
    # object. Today the object passed here is DockWindow. DockWindow should
    # satisfy that narrower SurfaceWindow contract, while SurfaceService should
    # depend only on that contract instead of importing the UI-layer DockWindow.
    @abstractmethod
    def configure_before_realize(self, window: object) -> None:
        """Configure platform surface role before the GTK window is realized."""

    @abstractmethod
    def on_realize(self, window: object) -> None:
        """Finish surface setup after realization."""

    @abstractmethod
    def set_workspace_scope(self, *, current_workspace_only: bool) -> None:
        """Show the dock on one workspace or all workspaces, if supported."""

    @abstractmethod
    def position_or_anchor(self, request: PlacementRequest) -> None:
        """Move an X11 window or configure native Wayland anchors."""

    @abstractmethod
    def set_reservation(self, request: ReservationRequest) -> None:
        """Reserve edge space for an always-visible dock."""

    @abstractmethod
    def clear_reservation(self) -> None:
        """Clear any edge-space reservation owned by this backend."""

    @abstractmethod
    def update_pointer_barrier(
        self,
        *,
        monitor: MonitorSnapshot | None,
        position: object,
        enabled: bool,
        pressure_callback: Callable[[], None] | None = None,
        pressure_threshold: int = 1,
    ) -> None:
        """Update or clear edge pointer barrier integration, if supported."""

    @abstractmethod
    def update_input_region(self, rect: Rect) -> None:
        """Update the interactive region for the dock surface, if supported."""

    @abstractmethod
    def set_blur_region(self, rect: Rect | None) -> None:
        """Set or clear a compositor blur hint, if supported."""

    @property
    def popups_use_parent_relative_coordinates(self) -> bool:
        """True when ``Gtk.Window.move()`` on a popup child uses
        parent-relative coordinates.

        On X11 ``move()`` always receives screen-absolute coordinates
        regardless of ``set_transient_for()``, so the default is
        ``False``.  Wayland compositor-positioned backends override this
        to ``True`` because xdg-popup coordinates are always relative to
        the parent surface.
        """
        return False

    def get_surface_position(self) -> tuple[int, int] | None:
        """Return the dock surface's screen (root) position, if known.

        Returns ``None`` when the backend cannot track the surface position,
        which means the caller should fall back to
        ``Gtk.Window.get_position()``.

        Backends that position the surface through the compositor (e.g.
        Wayland) are expected to override this because GTK always reports
        ``(0, 0)`` when it has no knowledge of the absolute placement.
        """
        return None


class VisibilityMonitor(ABC):
    """Runtime monitor for overlap-driven hide modes."""

    @abstractmethod
    def start(self) -> None:
        """Start monitoring foreign-window visibility."""

    @abstractmethod
    def stop(self) -> None:
        """Stop monitoring foreign-window visibility."""

    @abstractmethod
    def evaluate_now(self) -> None:
        """Force immediate reevaluation."""


class VisibilityService(Service):
    """Factory for overlap/dodge monitors."""

    @abstractmethod
    def create_monitor(
        self,
        *,
        get_dock_rect: Callable[[], Rect | None],
        on_change: Callable[[bool], None],
    ) -> VisibilityMonitor | None:
        """Create a monitor, or None when overlap tracking is unsupported."""


class PreviewService(Service):
    """Window-preview capture."""

    @abstractmethod
    def capture(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        """Capture a preview image for one window, if available."""

    @abstractmethod
    def thumbnail(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        """Return a compact thumbnail, including backend fallback if available."""


class WorkspaceService(Service):
    """Workspace list and switching operations."""

    @abstractmethod
    def list_workspaces(self) -> Sequence[WorkspaceSnapshot]:
        """Return known workspaces."""

    @abstractmethod
    def active_workspace(self) -> WorkspaceSnapshot | None:
        """Return the active workspace, if known."""

    @abstractmethod
    def activate(self, workspace_id: str) -> ActionResult:
        """Activate a workspace."""

    @abstractmethod
    def watch_active_workspace(self, on_change: Callable[[], None]) -> object | None:
        """Watch active workspace changes and return a backend-owned handle."""

    @abstractmethod
    def unwatch_active_workspace(self, handle: object) -> None:
        """Stop watching a handle returned by watch_active_workspace."""


class DesktopActionService(Service):
    """Desktop-wide window manager actions."""

    @abstractmethod
    def show_desktop(self, show: bool | None = None) -> ActionResult:
        """Show, hide, or toggle desktop visibility."""


class ScreenCaptureService(Service):
    """Screen color-picking operations."""

    @abstractmethod
    def pick_color(self, *, x: int, y: int) -> tuple[int, int, int] | None:
        """Pick a screen color in RGB byte values."""


class IdleService(Service):
    """Desktop idle-time source."""

    @abstractmethod
    def idle_seconds(self) -> float | None:
        """Return current idle time in seconds, if available."""


class WindowPickService(Service):
    """Window picking and process actions used by window-management applets."""

    @abstractmethod
    def pick_window_at(self, *, x: int, y: int) -> WindowSnapshot | None:
        """Return the topmost window at a screen point, if supported."""

    @abstractmethod
    def pid_for(self, window_id: WindowId) -> int | None:
        """Return the process ID for a window, if known."""

    @abstractmethod
    def kill(self, window_id: WindowId) -> ActionResult:
        """Terminate the process or window represented by the backend ID."""


class SessionBackend(ABC):
    """Selected platform backend for the current desktop session."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable backend name for logging and diagnostics."""

    @property
    @abstractmethod
    def display_server(self) -> DisplayServer:
        """Display-server family used by this backend."""

    @property
    @abstractmethod
    def capabilities(self) -> PlatformCapabilities:
        """Capabilities available in the selected runtime."""

    @property
    @abstractmethod
    def windows(self) -> WindowService:
        """Window/taskbar service."""

    @property
    @abstractmethod
    def surface(self) -> SurfaceService:
        """Dock surface service."""

    @property
    @abstractmethod
    def visibility(self) -> VisibilityService:
        """Overlap/dodge visibility service."""

    @property
    @abstractmethod
    def previews(self) -> PreviewService:
        """Window preview service."""

    @property
    @abstractmethod
    def workspaces(self) -> WorkspaceService | None:
        """Workspace service, when available."""

    @property
    @abstractmethod
    def desktop_actions(self) -> DesktopActionService | None:
        """Desktop action service, when available."""

    @property
    @abstractmethod
    def screen_capture(self) -> ScreenCaptureService | None:
        """Screen capture service, when available."""

    @property
    @abstractmethod
    def idle(self) -> IdleService | None:
        """Idle-time service, when available."""

    @property
    @abstractmethod
    def window_picker(self) -> WindowPickService | None:
        """Window-picking service, when available."""

    @abstractmethod
    def start(self) -> None:
        """Start all backend services needed for runtime operation."""

    @abstractmethod
    def stop(self) -> None:
        """Stop all backend services."""
