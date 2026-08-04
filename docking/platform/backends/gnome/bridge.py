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

"""GNOME Shell bridge services.

Docking cannot get full GNOME Wayland dock behavior from an ordinary GTK
client. This backend is the first bridge prototype: a GNOME Shell extension
exports shell-owned window/workspace facts over D-Bus, and Docking consumes
them through the same backend-neutral contracts used by X11 and Wayland.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from docking.log import get_logger
from docking.platform.app_matcher import AppIdMatcher
from docking.platform.backends.base import (
    ActionResult,
    DesktopActionService,
    DisplayServer,
    PlacementRequest,
    PreviewImage,
    PreviewService,
    Rect,
    ReservationRequest,
    SurfaceService,
    WindowId,
    WindowService,
    WindowSnapshot,
    WorkspaceService,
    WorkspaceSnapshot,
)
from docking.platform.running import (
    RunningAppInfo,
    RunningWindowInfo,
    RuntimeAppIdentity,
)

if TYPE_CHECKING:
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel

log = get_logger(name="backend.gnome.bridge")

BUS_NAME = "org.docking.Docking.GnomeShellBridge"
OBJECT_PATH = "/org/docking/Docking/GnomeShellBridge"
INTERFACE = "org.docking.Docking.GnomeShellBridge1"
_DOCK_POSITION_RETRY_MS = 100
_DOCK_POSITION_RETRY_ATTEMPTS = 15
_WORKSPACE_INITIAL_RETRY_MS = 100
_WORKSPACE_INITIAL_RETRY_ATTEMPTS = 15


class GnomeShellBridgeClient:
    """Synchronous session-bus client for the GNOME Shell bridge extension."""

    def __init__(self, proxy: Gio.DBusProxy) -> None:
        self._proxy = proxy

    @classmethod
    def connect(cls) -> GnomeShellBridgeClient | None:
        """Return a bridge client when the extension D-Bus name is available."""
        try:
            proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None,
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE,
                None,
            )
            proxy.call_sync(
                "ListWindows",
                None,
                Gio.DBusCallFlags.NO_AUTO_START,
                1000,
                None,
            )
        except Exception as exc:
            log.info("GNOME Shell bridge unavailable: %s", exc)
            return None
        return cls(proxy=proxy)

    def list_windows(self) -> Sequence[Mapping[str, Any]]:
        """Return raw window rows exported by the Shell extension."""
        result = self._call("ListWindows")
        if result is None:
            return ()
        return _json_rows(result.unpack()[0])

    def list_workspaces(self) -> Sequence[Mapping[str, Any]]:
        """Return raw workspace rows exported by the Shell extension."""
        result = self._call("ListWorkspaces")
        if result is None:
            return ()
        return _json_rows(result.unpack()[0])

    def activate(self, bridge_id: int) -> bool:
        return self._call_window_action("Activate", bridge_id)

    def minimize(self, bridge_id: int) -> bool:
        return self._call_window_action("Minimize", bridge_id)

    def close(self, bridge_id: int) -> bool:
        return self._call_window_action("Close", bridge_id)

    def position_dock(self, x: int, y: int, width: int, height: int) -> bool:
        """Ask the GNOME Shell extension to position the dock window via Mutter."""
        result = self._call(
            "PositionDock",
            GLib.Variant("(iiii)", (x, y, width, height)),
        )
        return bool(result.unpack()[0]) if result is not None else False

    def capture_window(
        self, bridge_id: int, *, width: int, height: int
    ) -> bytes | None:
        """Request a PNG thumbnail of a window from the GNOME Shell extension.

        Returns raw PNG bytes, or ``None`` when capture is unavailable.
        """
        result = self._call(
            "CaptureWindow",
            GLib.Variant("(uii)", (bridge_id, width, height)),
        )
        if result is None:
            return None
        try:
            png_base64 = result.unpack()[0]
        except (TypeError, ValueError, IndexError):
            return None
        if not png_base64:
            return None
        return GLib.base64_decode(png_base64)

    def show_desktop(self) -> bool:
        """Toggle show-desktop state via the GNOME Shell extension."""
        result = self._call("ShowDesktop")
        if result is None:
            return False
        try:
            return bool(result.unpack()[0])
        except (TypeError, ValueError, IndexError):
            return False

    def activate_workspace(self, workspace_id: str) -> bool:
        try:
            bridge_id = int(workspace_id)
        except (TypeError, ValueError):
            return False
        result = self._call("ActivateWorkspace", GLib.Variant("(u)", (bridge_id,)))
        return bool(result.unpack()[0]) if result is not None else False

    def subscribe_changed(self, callback: Callable[[], None]) -> int | None:
        """Subscribe to extension state changes."""
        connection = self._proxy.get_connection()
        if connection is None:
            return None

        def handle_signal(
            _connection: Gio.DBusConnection,
            _sender_name: str,
            _object_path: str,
            _interface_name: str,
            _signal_name: str,
            _parameters: GLib.Variant,
        ) -> None:
            callback()

        return connection.signal_subscribe(
            BUS_NAME,
            INTERFACE,
            "Changed",
            OBJECT_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            handle_signal,
        )

    def unsubscribe_changed(self, handle: object) -> None:
        connection = self._proxy.get_connection()
        if connection is None or not isinstance(handle, int):
            return
        connection.signal_unsubscribe(handle)

    def _call(
        self, method: str, parameters: GLib.Variant | None = None
    ) -> GLib.Variant | None:
        try:
            return self._proxy.call_sync(
                method,
                parameters,
                Gio.DBusCallFlags.NO_AUTO_START,
                1000,
                None,
            )
        except Exception as exc:
            log.warning("GNOME Shell bridge call %s failed: %s", method, exc)
            return None

    def _call_window_action(self, method: str, bridge_id: int) -> bool:
        result = self._call(method, GLib.Variant("(u)", (bridge_id,)))
        return bool(result.unpack()[0]) if result is not None else False


@dataclass(frozen=True)
class _BridgeWindow:
    bridge_id: int
    title: str
    app_id: str
    desktop_id: str | None
    active: bool
    minimized: bool
    maximized: bool
    fullscreen: bool
    monitor: int | None
    workspace_id: str | None
    geometry: Rect | None
    pid: int | None
    runtime_app: RuntimeAppIdentity | None

    @property
    def window_id(self) -> WindowId:
        return WindowId(backend=DisplayServer.WAYLAND, value=f"gnome:{self.bridge_id}")


class GnomeShellBridgeWindowService(WindowService):
    """WindowService backed by the GNOME Shell bridge extension."""

    def __init__(
        self,
        *,
        model: DockModel,
        launcher: Launcher,
        bridge: object,
    ) -> None:
        self._model = model
        self._matcher = AppIdMatcher(launcher=launcher)
        self._bridge = bridge
        self._windows_by_id: dict[int, _BridgeWindow] = {}
        self._changed_handle: object | None = None
        self._poll_source_id = 0

    def start(self) -> None:
        subscribe = getattr(self._bridge, "subscribe_changed", None)
        if callable(subscribe):
            self._changed_handle = subscribe(self.refresh)
        self.refresh()
        self._poll_source_id = GLib.timeout_add_seconds(2, self._poll)

    def stop(self) -> None:
        if self._poll_source_id:
            GLib.source_remove(self._poll_source_id)
            self._poll_source_id = 0
        unsubscribe = getattr(self._bridge, "unsubscribe_changed", None)
        if callable(unsubscribe) and self._changed_handle is not None:
            unsubscribe(self._changed_handle)
        self._changed_handle = None
        self._windows_by_id.clear()
        self._model.update_running(running={})

    def refresh(self) -> None:
        """Refresh cached bridge state and publish running app state."""
        self._matcher.sync_visible_items(self._model.visible_items())
        rows = self._bridge.list_windows()
        windows: dict[int, _BridgeWindow] = {}
        for row in rows:
            window = self._window_from_row(row)
            if window is not None:
                windows[window.bridge_id] = window
        self._windows_by_id = windows
        self._publish_running()

    def list_all_windows(self) -> Sequence[WindowSnapshot]:
        return tuple(
            self._snapshot_for(window) for window in self._windows_by_id.values()
        )

    def list_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        return tuple(
            self._snapshot_for(window)
            for window in self._windows_by_id.values()
            if window.desktop_id == desktop_id
        )

    def list_preview_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        return self.list_windows(desktop_id=desktop_id)

    def icon_name_for_desktop(self, desktop_id: str) -> str:
        return "application-x-executable"

    def activate(self, window_id: WindowId) -> ActionResult:
        window = self._window_for_id(window_id)
        if window is None:
            return ActionResult.NOT_FOUND
        return _action_result(self._bridge.activate(window.bridge_id))

    def activate_most_recent(self, desktop_id: str) -> ActionResult:
        window = self._first_window_for_desktop(desktop_id)
        if window is None:
            return ActionResult.NOT_FOUND
        return _action_result(self._bridge.activate(window.bridge_id))

    def cycle(self, desktop_id: str) -> ActionResult:
        return self.activate_most_recent(desktop_id=desktop_id)

    def minimize_all(self, desktop_id: str) -> ActionResult:
        windows = self._windows_for_desktop(desktop_id)
        if not windows:
            return ActionResult.NOT_FOUND
        result = ActionResult.OK
        for window in windows:
            if not self._bridge.minimize(window.bridge_id):
                result = ActionResult.FAILED
        return result

    def close(self, window_id: WindowId) -> ActionResult:
        window = self._window_for_id(window_id)
        if window is None:
            return ActionResult.NOT_FOUND
        return _action_result(self._bridge.close(window.bridge_id))

    def close_all(self, desktop_id: str) -> ActionResult:
        windows = self._windows_for_desktop(desktop_id)
        if not windows:
            return ActionResult.NOT_FOUND
        result = ActionResult.OK
        for window in windows:
            if not self._bridge.close(window.bridge_id):
                result = ActionResult.FAILED
        return result

    def close_focused(self, desktop_id: str) -> ActionResult:
        windows = self._windows_for_desktop(desktop_id)
        if not windows:
            return ActionResult.NOT_FOUND
        window = next((item for item in windows if item.active), windows[0])
        return _action_result(self._bridge.close(window.bridge_id))

    def toggle_focus(self, desktop_id: str) -> ActionResult:
        window = self._first_window_for_desktop(desktop_id)
        if window is None:
            return ActionResult.NOT_FOUND
        if window.active and self._bridge.minimize(window.bridge_id):
            return ActionResult.OK
        return _action_result(self._bridge.activate(window.bridge_id))

    def _poll(self) -> bool:
        self.refresh()
        return True

    def _window_from_row(self, row: Mapping[str, Any]) -> _BridgeWindow | None:
        bridge_id = _int_from_row(row, "id")
        if bridge_id is None:
            return None
        app_id = _str_from_row(row, "app-id")
        pid = _int_from_row(row, "pid")
        if pid is not None and pid <= 0:
            pid = None
        match = self._matcher.match_result(app_id, process_id=pid) if app_id else None
        desktop_id = match.desktop_id if match is not None else None
        geometry = _rect_from_row(row)
        return _BridgeWindow(
            bridge_id=bridge_id,
            title=_str_from_row(row, "title") or "Window",
            app_id=app_id,
            desktop_id=desktop_id,
            active=_bool_from_row(row, "active"),
            minimized=_bool_from_row(row, "minimized"),
            maximized=_bool_from_row(row, "maximized"),
            fullscreen=_bool_from_row(row, "fullscreen"),
            monitor=_int_from_row(row, "monitor"),
            workspace_id=_workspace_id_from_row(row),
            geometry=geometry,
            pid=pid,
            runtime_app=match.runtime_app if match is not None else None,
        )

    def _publish_running(self) -> None:
        windows_by_desktop: dict[str, list[RunningWindowInfo]] = {}
        for window in self._windows_by_id.values():
            if window.desktop_id is None:
                continue
            windows_by_desktop.setdefault(window.desktop_id, []).append(
                RunningWindowInfo(
                    desktop_id=window.desktop_id,
                    xid=window.bridge_id,
                    window_id=window.window_id,
                    active=window.active,
                    urgent=False,
                    window=window.bridge_id,
                    runtime_app=window.runtime_app,
                )
            )
        self._model.update_running(
            running={
                desktop_id: RunningAppInfo.from_windows(items)
                for desktop_id, items in windows_by_desktop.items()
            }
        )

    def _snapshot_for(self, window: _BridgeWindow) -> WindowSnapshot:
        return WindowSnapshot(
            id=window.window_id,
            desktop_id=window.desktop_id or "",
            title=window.title,
            app_id=window.app_id or None,
            active=window.active,
            minimized=window.minimized,
            maximized=window.maximized,
            fullscreen=window.fullscreen,
            geometry=window.geometry,
            workspace_id=window.workspace_id,
            can_activate=True,
            can_minimize=True,
            can_close=True,
            can_preview=True,
        )

    def _windows_for_desktop(self, desktop_id: str) -> list[_BridgeWindow]:
        return [
            window
            for window in self._windows_by_id.values()
            if window.desktop_id == desktop_id
        ]

    def _first_window_for_desktop(self, desktop_id: str) -> _BridgeWindow | None:
        windows = self._windows_for_desktop(desktop_id)
        if not windows:
            return None
        return next((window for window in windows if window.active), windows[0])

    def _window_for_id(self, window_id: WindowId) -> _BridgeWindow | None:
        if window_id.backend is not DisplayServer.WAYLAND:
            return None
        value = str(window_id.value)
        if not value.startswith("gnome:"):
            return None
        try:
            bridge_id = int(value.removeprefix("gnome:"))
        except ValueError:
            return None
        return self._windows_by_id.get(bridge_id)


class GnomeShellBridgeWorkspaceService(WorkspaceService):
    """WorkspaceService backed by the GNOME Shell bridge extension."""

    def __init__(self, *, bridge: object) -> None:
        self._bridge = bridge
        self._workspaces: tuple[WorkspaceSnapshot, ...] = ()
        self._watchers: dict[int, Callable[[], None]] = {}
        self._next_watch_id = 1
        self._changed_handle: object | None = None
        self._poll_source_id = 0
        self._initial_retry_source_id = 0
        self._initial_retry_attempts_remaining = 0
        self._started = False

    def start(self) -> None:
        self._started = True
        subscribe = getattr(self._bridge, "subscribe_changed", None)
        if callable(subscribe):
            self._changed_handle = subscribe(self.refresh)
        self.refresh()
        self._ensure_initial_active_workspace()
        self._poll_source_id = GLib.timeout_add_seconds(2, self._poll)

    def stop(self) -> None:
        self._started = False
        if self._initial_retry_source_id:
            GLib.source_remove(self._initial_retry_source_id)
            self._initial_retry_source_id = 0
        self._initial_retry_attempts_remaining = 0
        if self._poll_source_id:
            GLib.source_remove(self._poll_source_id)
            self._poll_source_id = 0
        unsubscribe = getattr(self._bridge, "unsubscribe_changed", None)
        if callable(unsubscribe) and self._changed_handle is not None:
            unsubscribe(self._changed_handle)
        self._changed_handle = None
        self._watchers.clear()
        self._workspaces = ()

    def refresh(self) -> None:
        previous_signature = _workspace_signature(self._workspaces)
        rows = self._bridge.list_workspaces()
        self._workspaces = tuple(
            workspace
            for row in rows
            if (workspace := _workspace_from_row(row)) is not None
        )
        if previous_signature == _workspace_signature(self._workspaces):
            return
        for callback in tuple(self._watchers.values()):
            callback()

    def list_workspaces(self) -> Sequence[WorkspaceSnapshot]:
        if not self._workspaces or self.active_workspace() is None:
            self.refresh()
        return self._workspaces

    def active_workspace(self) -> WorkspaceSnapshot | None:
        return next(
            (workspace for workspace in self._workspaces if workspace.active),
            None,
        )

    def activate(self, workspace_id: str) -> ActionResult:
        return _action_result(self._bridge.activate_workspace(workspace_id))

    def watch_active_workspace(self, on_change: Callable[[], None]) -> object | None:
        handle = self._next_watch_id
        self._next_watch_id += 1
        self._watchers[handle] = on_change
        return handle

    def unwatch_active_workspace(self, handle: object) -> None:
        if isinstance(handle, int):
            self._watchers.pop(handle, None)

    def _poll(self) -> bool:
        self.refresh()
        return True

    def _ensure_initial_active_workspace(self) -> None:
        if not self._started or self.active_workspace() is not None:
            return
        if self._initial_retry_source_id:
            return
        self._initial_retry_attempts_remaining = _WORKSPACE_INITIAL_RETRY_ATTEMPTS
        self._initial_retry_source_id = GLib.timeout_add(
            _WORKSPACE_INITIAL_RETRY_MS,
            self._retry_initial_active_workspace,
        )

    def _retry_initial_active_workspace(self) -> bool:
        self._initial_retry_attempts_remaining -= 1
        self.refresh()
        if (
            self.active_workspace() is not None
            or self._initial_retry_attempts_remaining <= 0
        ):
            self._initial_retry_source_id = 0
            self._initial_retry_attempts_remaining = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE


def _action_result(ok: bool) -> ActionResult:
    return ActionResult.OK if ok else ActionResult.FAILED


def _variant_value(value: Any) -> Any:
    return value.unpack() if hasattr(value, "unpack") else value


def _json_rows(value: object) -> tuple[Mapping[str, Any], ...]:
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError as exc:
        log.warning("GNOME Shell bridge returned invalid JSON: %s", exc)
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(row for row in decoded if isinstance(row, dict))


def _row_value(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    return _variant_value(row.get(key, default))


def _str_from_row(row: Mapping[str, Any], key: str) -> str:
    value = _row_value(row, key, "")
    return str(value).strip() if value is not None else ""


def _bool_from_row(row: Mapping[str, Any], key: str) -> bool:
    return bool(_row_value(row, key, False))


def _int_from_row(row: Mapping[str, Any], key: str) -> int | None:
    value = _row_value(row, key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _workspace_id_from_row(row: Mapping[str, Any]) -> str | None:
    value = _int_from_row(row, "workspace")
    return str(value) if value is not None else None


def _rect_from_row(row: Mapping[str, Any]) -> Rect | None:
    x = _int_from_row(row, "x")
    y = _int_from_row(row, "y")
    width = _int_from_row(row, "width")
    height = _int_from_row(row, "height")
    if x is None or y is None or width is None or height is None:
        return None
    return Rect(x=x, y=y, width=width, height=height)


def _workspace_from_row(row: Mapping[str, Any]) -> WorkspaceSnapshot | None:
    workspace_id = _int_from_row(row, "id")
    if workspace_id is None:
        return None
    number = _int_from_row(row, "index")
    return WorkspaceSnapshot(
        id=str(workspace_id),
        number=number if number is not None else workspace_id,
        name=_str_from_row(row, "name"),
        active=_bool_from_row(row, "active"),
    )


def _workspace_signature(
    workspaces: Sequence[WorkspaceSnapshot],
) -> tuple[tuple[str, int, str, bool], ...]:
    return tuple(
        (workspace.id, workspace.number, workspace.name, workspace.active)
        for workspace in workspaces
    )


class GnomeShellBridgeSurfaceService(SurfaceService):
    """SurfaceService that delegates edge positioning to the GNOME Shell extension.

    On Wayland the compositor ignores client-side ``gtk_window_move()``, so
    ordinary GTK positioning is a no-op.  This service asks the shell
    extension to reposition the dock window via Mutter's own
    ``Meta.Window.move_resize_frame()``, which works because the extension
    runs inside the shell process with full window-management privileges.
    """

    def __init__(self, *, bridge: object) -> None:
        self._bridge = bridge
        self._window: object | None = None
        self._position_retry_source_id: int = 0
        self._position_retry_attempts_remaining: int = 0
        self._latest_position_request: PlacementRequest | None = None
        # Wayland: GTK does not know the absolute screen position.
        # We track it here so get_surface_position() can return it.
        self._surface_x: int | None = None
        self._surface_y: int | None = None

    # -- SurfaceService ---------------------------------------------------

    def start(self) -> None:
        pass

    @property
    def popups_use_parent_relative_coordinates(self) -> bool:
        return True

    def stop(self) -> None:
        if self._position_retry_source_id:
            GLib.source_remove(self._position_retry_source_id)
            self._position_retry_source_id = 0
        self._position_retry_attempts_remaining = 0
        self._latest_position_request = None
        self._window = None
        self._surface_x = None
        self._surface_y = None

    def get_surface_position(self) -> tuple[int, int] | None:
        if self._surface_x is not None and self._surface_y is not None:
            return self._surface_x, self._surface_y
        return None

    def configure_before_realize(self, window: object) -> None:
        self._window = window
        _apply_dock_window_hints(window)

    def on_realize(self, window: object) -> None:
        self._window = window
        _apply_dock_window_hints(window)

    def position_or_anchor(self, request: PlacementRequest) -> None:
        window = self._window
        if window is None:
            return
        _call_method(
            window, "set_size_request", request.size.width, request.size.height
        )
        _call_method(window, "resize", request.size.width, request.size.height)

        self._latest_position_request = request
        self._position_retry_attempts_remaining = _DOCK_POSITION_RETRY_ATTEMPTS
        self._apply_position_request(request)
        self._schedule_position_retry()

    def set_workspace_scope(self, *, current_workspace_only: bool) -> None:
        pass

    def set_reservation(self, request: ReservationRequest) -> None:
        pass

    def clear_reservation(self) -> None:
        pass

    def update_pointer_barrier(
        self,
        *,
        monitor: Any = None,
        position: Any = None,
        enabled: bool = False,
        pressure_callback: Any = None,
        pressure_threshold: int = 1,
    ) -> None:
        pass

    def update_input_region(self, rect: Rect) -> None:
        pass

    def set_blur_region(self, rect: Rect | None) -> None:
        pass

    # -- internals --------------------------------------------------------

    def _retry_latest_position(self) -> bool:
        self._position_retry_source_id = 0
        request = self._latest_position_request
        if request is None or self._position_retry_attempts_remaining <= 0:
            return False

        self._position_retry_attempts_remaining -= 1
        self._apply_position_request(request)
        self._schedule_position_retry()
        return False

    def _schedule_position_retry(self) -> None:
        if self._position_retry_source_id:
            return
        if (
            self._latest_position_request is None
            or self._position_retry_attempts_remaining <= 0
        ):
            return
        self._position_retry_source_id = GLib.timeout_add(
            _DOCK_POSITION_RETRY_MS, self._retry_latest_position
        )

    def _apply_position_request(self, request: PlacementRequest) -> bool:
        # Track the compositor-assigned position so get_surface_position()
        # can report it.  GTK's get_position() returns (0,0) on Wayland.
        self._surface_x = request.x
        self._surface_y = request.y
        return bool(
            self._bridge.position_dock(
                request.x, request.y, request.size.width, request.size.height
            )
        )


# -- surface helpers (mirror reduced/services.py) ----------------------------


def _apply_dock_window_hints(window: object) -> None:
    _call_method(window, "set_skip_taskbar_hint", True)
    _call_method(window, "set_skip_pager_hint", True)
    _call_method(window, "set_accept_focus", False)
    _call_method(window, "set_focus_on_map", False)
    _call_method(window, "stick")
    _call_method(window, "set_keep_above", True)
    _set_dock_type_hint(window)


def _call_method(target: object, method_name: str, *args: object) -> None:
    method = getattr(target, method_name, None)
    if callable(method):
        method(*args)


def _set_dock_type_hint(window: object) -> None:
    set_type_hint = getattr(window, "set_type_hint", None)
    if not callable(set_type_hint):
        return
    try:
        from gi.repository import Gdk
    except (ImportError, ValueError):
        return
    set_type_hint(Gdk.WindowTypeHint.DOCK)


# -- preview service ----------------------------------------------------------


class GnomeShellBridgePreviewService(PreviewService):
    """Window preview capture backed by the GNOME Shell bridge extension.

    On Wayland only the compositor can read window pixels.  This service
    delegates to the bridge extension, which calls
    ``Meta.WindowActor.get_image()`` inside the shell process.
    """

    def __init__(self, *, bridge: GnomeShellBridgeClient) -> None:
        self._bridge = bridge

    def start(self) -> None:
        """Nothing to initialise."""

    def stop(self) -> None:
        """No persistent resources."""

    def capture(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        return self._do_capture(window_id, width, height)

    def thumbnail(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        return self._do_capture(window_id, width, height)

    # -- internals --------------------------------------------------------

    def _do_capture(
        self, window_id: WindowId, width: int, height: int
    ) -> PreviewImage | None:
        bridge_id = self._bridge_id(window_id)
        if bridge_id is None:
            return None
        png_bytes = self._bridge.capture_window(bridge_id, width=width, height=height)
        if not png_bytes:
            return None
        # Decode PNG → GdkPixbuf so the UI can use it directly.
        return self._pixbuf_from_png(png_bytes, width, height)

    @staticmethod
    def _bridge_id(window_id: WindowId) -> int | None:
        if window_id.backend is not DisplayServer.WAYLAND:
            return None
        value = str(window_id.value)
        if not value.startswith("gnome:"):
            return None
        try:
            return int(value.removeprefix("gnome:"))
        except ValueError:
            return None

    @staticmethod
    def _pixbuf_from_png(data: bytes, width: int, height: int) -> PreviewImage | None:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf

        try:
            loader = GdkPixbuf.PixbufLoader.new_with_type("png")
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
        except Exception:
            return None
        if pixbuf is None:
            return None
        return PreviewImage(image=pixbuf, width=width, height=height)


# -- desktop actions service -------------------------------------------------


class GnomeShellBridgeDesktopActionService(DesktopActionService):
    """Show-desktop toggle backed by the GNOME Shell bridge extension."""

    def __init__(self, *, bridge: GnomeShellBridgeClient) -> None:
        self._bridge = bridge

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def show_desktop(self, show: bool | None = None) -> ActionResult:
        ok = self._bridge.show_desktop()
        return ActionResult.OK if ok else ActionResult.FAILED
