# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Wayfire IPC helpers and services.

Wayfire's ``ipc`` plugin exposes a Unix socket at ``$WAYFIRE_SOCKET``.  The
wire format is a native-endian 4-byte JSON length prefix followed by a JSON
object with ``method`` and ``data`` members.  The ``ipc-rules`` plugin registers
the ``window-rules/*`` methods used here for windows, outputs, and workspace
sets.

This backend deliberately starts from the stable snapshot/action surface:
``list-views``, ``get-focused-view``, ``focus-view``, ``close-view``,
``list-outputs``, and ``list-wsets``.  Wayfire also has event watching, but
snapshot refreshes after lifecycle/actions are enough for a conservative first
integration and keep the service easy to reason about.
"""

from __future__ import annotations

import json
import os
import socket
import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docking.log import get_logger
from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    Rect,
    WindowId,
    WindowPickService,
    WindowService,
    WindowSnapshot,
    WorkspaceService,
    WorkspaceSnapshot,
)
from docking.platform.backends.wayland.toplevels import WaylandAppIdMatcher
from docking.platform.running import RunningAppInfo, RunningWindowInfo

log = get_logger(name="wayfire_ipc")

try:
    from gi.repository import GLib
except (ImportError, ValueError):
    GLib = None

WAYFIRE_WINDOW_POLL_SECONDS = 2


@dataclass(frozen=True)
class WayfireWindowRecord:
    """Backend-neutral projection of one Wayfire view."""

    id: int
    title: str
    app_id: str
    desktop_id: str | None
    active: bool
    minimized: bool | None
    fullscreen: bool | None
    geometry: Rect | None
    workspace_id: str | None
    pid: int | None

    @property
    def window_id(self) -> WindowId:
        return WindowId(backend=DisplayServer.WAYLAND, value=str(self.id))


@dataclass(frozen=True)
class WayfireWorkspaceRecord:
    """One logical workspace cell from a Wayfire workspace set."""

    id: str
    number: int
    name: str
    active: bool


class WayfireIpcClient:
    """Short-lived request client for Wayfire's length-prefixed JSON IPC."""

    def __init__(
        self,
        *,
        socket_path: str | Path,
        socket_factory: Callable[[int, int], socket.socket] = socket.socket,
        timeout: float = 1.0,
    ) -> None:
        self._path = str(socket_path)
        self._socket_factory = socket_factory
        self._timeout = timeout

    def request(self, method: str, data: Mapping[str, Any] | None = None) -> Any:
        """Call one Wayfire IPC method and return the decoded JSON response."""
        payload = json.dumps(
            {
                "method": method,
                "data": dict(data or {}),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        with self._socket_factory(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self._timeout)
            sock.connect(self._path)
            sock.sendall(struct.pack("I", len(payload)) + payload)
            response_len = struct.unpack("I", _read_exact(sock, 4))[0]
            response = _read_exact(sock, response_len)
        return json.loads(response.decode("utf-8"))

    def action(self, method: str, data: Mapping[str, Any]) -> ActionResult:
        """Call an action method and map Wayfire's result/error to ActionResult."""
        try:
            response = self.request(method, data)
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            log.info("Wayfire IPC action failed: %s", exc)
            return ActionResult.FAILED
        if isinstance(response, Mapping) and response.get("result") == "ok":
            return ActionResult.OK
        if isinstance(response, Mapping) and "error" in response:
            return ActionResult.NOT_FOUND
        return ActionResult.FAILED


class WayfireWindowService(WindowService):
    """WindowService backed by Wayfire ``ipc-rules`` snapshots."""

    def __init__(
        self,
        *,
        model,
        launcher,
        client: WayfireIpcClient,
    ) -> None:
        self._model = model
        self._matcher = WaylandAppIdMatcher(launcher=launcher)
        self._client = client
        self._records: dict[int, WayfireWindowRecord] = {}
        self._poll_source_id = 0

    def start(self) -> None:
        self._refresh()
        if GLib is not None and self._poll_source_id == 0:
            self._poll_source_id = GLib.timeout_add_seconds(
                WAYFIRE_WINDOW_POLL_SECONDS,
                self._poll,
            )

    def stop(self) -> None:
        if GLib is not None and self._poll_source_id:
            GLib.source_remove(self._poll_source_id)
        self._poll_source_id = 0
        self._records.clear()
        self._model.update_running(running={})

    def list_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        return tuple(
            self._snapshot_for(record)
            for record in self._records.values()
            if record.desktop_id == desktop_id
        )

    def list_preview_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        return self.list_windows(desktop_id=desktop_id)

    def icon_name_for_desktop(self, desktop_id: str) -> str:
        return "application-x-executable"

    def activate(self, window_id: WindowId) -> ActionResult:
        wayfire_id = _wayfire_window_id(window_id)
        if wayfire_id is None or wayfire_id not in self._records:
            return ActionResult.NOT_FOUND
        result = self._client.action("window-rules/focus-view", {"id": wayfire_id})
        if result is ActionResult.OK:
            self._refresh()
        return result

    def activate_most_recent(self, desktop_id: str) -> ActionResult:
        record = self._first_record_for_desktop(desktop_id)
        if record is None:
            return ActionResult.NOT_FOUND
        return self.activate(record.window_id)

    def cycle(self, desktop_id: str) -> ActionResult:
        return self.activate_most_recent(desktop_id=desktop_id)

    def minimize_all(self, desktop_id: str) -> ActionResult:
        return ActionResult.UNSUPPORTED

    def close(self, window_id: WindowId) -> ActionResult:
        wayfire_id = _wayfire_window_id(window_id)
        if wayfire_id is None or wayfire_id not in self._records:
            return ActionResult.NOT_FOUND
        result = self._client.action("window-rules/close-view", {"id": wayfire_id})
        if result is ActionResult.OK:
            self._refresh()
        return result

    def close_all(self, desktop_id: str) -> ActionResult:
        records = self._records_for_desktop(desktop_id)
        if not records:
            return ActionResult.NOT_FOUND
        result = ActionResult.OK
        for record in records:
            action = self.close(record.window_id)
            if action is not ActionResult.OK:
                result = action
        return result

    def close_focused(self, desktop_id: str) -> ActionResult:
        record = self._first_record_for_desktop(desktop_id)
        if record is None:
            return ActionResult.NOT_FOUND
        return self.close(record.window_id)

    def toggle_focus(self, desktop_id: str) -> ActionResult:
        record = self._first_record_for_desktop(desktop_id)
        if record is None:
            return ActionResult.NOT_FOUND
        if record.active:
            return ActionResult.OK
        return self.activate(record.window_id)

    def _refresh(self) -> None:
        try:
            views = self._client.request("window-rules/list-views")
            focused = self._client.request("window-rules/get-focused-view")
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            log.info("Wayfire IPC snapshot unavailable: %s", exc)
            return
        focused_id = _focused_view_id(focused)
        if not isinstance(views, list):
            views = []
        self._matcher.sync_visible_items(self._model.visible_items())
        records: dict[int, WayfireWindowRecord] = {}
        for item in views:
            if not isinstance(item, Mapping):
                continue
            record = _record_from_view(
                item,
                matcher=self._matcher,
                focused_id=focused_id,
            )
            if record is not None:
                records[record.id] = record
        self._records = records
        self._publish_running()

    def _publish_running(self) -> None:
        windows_by_desktop: dict[str, list[RunningWindowInfo]] = {}
        for record in self._records.values():
            if record.desktop_id is None:
                continue
            windows_by_desktop.setdefault(record.desktop_id, []).append(
                RunningWindowInfo(
                    desktop_id=record.desktop_id,
                    xid=0,
                    window_id=record.window_id,
                    active=record.active,
                    urgent=False,
                    window=str(record.id),
                )
            )
        self._model.update_running(
            running={
                desktop_id: RunningAppInfo.from_windows(items)
                for desktop_id, items in windows_by_desktop.items()
            }
        )

    def _poll(self) -> bool:
        self._refresh()
        return True

    def _snapshot_for(self, record: WayfireWindowRecord) -> WindowSnapshot:
        return WindowSnapshot(
            id=record.window_id,
            desktop_id=record.desktop_id or "",
            title=record.title,
            app_id=record.app_id or None,
            active=record.active,
            urgent=False,
            minimized=record.minimized,
            fullscreen=record.fullscreen,
            geometry=record.geometry,
            workspace_id=record.workspace_id,
            can_activate=True,
            can_minimize=False,
            can_close=True,
            can_preview=False,
        )

    def _records_for_desktop(self, desktop_id: str) -> list[WayfireWindowRecord]:
        return [
            record
            for record in self._records.values()
            if record.desktop_id == desktop_id
        ]

    def _first_record_for_desktop(self, desktop_id: str) -> WayfireWindowRecord | None:
        records = self._records_for_desktop(desktop_id)
        if not records:
            return None
        return next((record for record in records if record.active), records[0])


class WayfireWorkspaceService(WorkspaceService):
    """WorkspaceService backed by Wayfire output/workspace-set snapshots."""

    def __init__(self, *, client: WayfireIpcClient) -> None:
        self._client = client
        self._records: dict[str, WayfireWorkspaceRecord] = {}
        self._watchers: dict[int, Callable[[], None]] = {}
        self._next_watch_id = 1
        self._active_ids: tuple[str, ...] = ()

    def start(self) -> None:
        self._refresh()

    def stop(self) -> None:
        self._records.clear()
        self._watchers.clear()
        self._active_ids = ()

    def list_workspaces(self) -> Sequence[WorkspaceSnapshot]:
        self._refresh()
        return tuple(
            WorkspaceSnapshot(
                id=record.id,
                number=record.number,
                name=record.name,
                active=record.active,
            )
            for record in sorted(self._records.values(), key=lambda r: r.number)
        )

    def active_workspace(self) -> WorkspaceSnapshot | None:
        return next(
            (workspace for workspace in self.list_workspaces() if workspace.active),
            None,
        )

    def activate(self, workspace_id: str) -> ActionResult:
        return ActionResult.UNSUPPORTED

    def watch_active_workspace(self, on_change: Callable[[], None]) -> object | None:
        watch_id = self._next_watch_id
        self._next_watch_id += 1
        self._watchers[watch_id] = on_change
        return watch_id

    def unwatch_active_workspace(self, handle: object) -> None:
        if isinstance(handle, int):
            self._watchers.pop(handle, None)

    def _refresh(self) -> None:
        try:
            outputs = self._client.request("window-rules/list-outputs")
            focused_output = self._client.request("window-rules/get-focused-output")
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            log.info("Wayfire workspace snapshot unavailable: %s", exc)
            return
        focused_output_id = _focused_output_id(focused_output)
        records: list[WayfireWorkspaceRecord] = []
        output_items = outputs if isinstance(outputs, list) else []
        for output in output_items:
            if not isinstance(output, Mapping):
                continue
            records.extend(
                _workspace_records_from_output(
                    output,
                    number_offset=len(records),
                    focused_output_id=focused_output_id,
                )
            )
        self._records = {record.id: record for record in records}
        self._notify_if_active_changed()

    def _notify_if_active_changed(self) -> None:
        active_ids = tuple(
            record.id
            for record in sorted(self._records.values(), key=lambda r: r.number)
            if record.active
        )
        if active_ids == self._active_ids:
            return
        self._active_ids = active_ids
        for callback in tuple(self._watchers.values()):
            callback()


class WayfireWindowPickService(WindowPickService):
    """WindowPickService backed by Wayfire view geometry and PID metadata."""

    def __init__(self, *, client: WayfireIpcClient) -> None:
        self._client = client
        self._records: dict[int, WayfireWindowRecord] = {}

    def start(self) -> None:
        self._refresh()

    def stop(self) -> None:
        self._records.clear()

    def pick_window_at(self, *, x: int, y: int) -> WindowSnapshot | None:
        self._refresh()
        candidates = [
            record
            for record in self._records.values()
            if record.geometry is not None
            and record.geometry.x <= x < record.geometry.right
            and record.geometry.y <= y < record.geometry.bottom
            and not record.minimized
        ]
        if not candidates:
            return None
        # Current Wayfire list-views output is bottom-to-top; use the last
        # matching toplevel as the picked target.
        return self._snapshot_for(candidates[-1])

    def pid_for(self, window_id: WindowId) -> int | None:
        wayfire_id = _wayfire_window_id(window_id)
        if wayfire_id is None:
            return None
        record = self._records.get(wayfire_id)
        if record is None:
            self._refresh()
            record = self._records.get(wayfire_id)
        if record is None:
            return None
        return record.pid if record.pid and record.pid > 0 else None

    def kill(self, window_id: WindowId) -> ActionResult:
        from docking.applets.windowkiller.state import kill_pid

        pid = self.pid_for(window_id)
        if pid is None:
            return ActionResult.NOT_FOUND
        return ActionResult.OK if kill_pid(pid=pid) else ActionResult.FAILED

    def _refresh(self) -> None:
        try:
            views = self._client.request("window-rules/list-views")
            focused = self._client.request("window-rules/get-focused-view")
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            log.info("Wayfire pick snapshot unavailable: %s", exc)
            return
        focused_id = _focused_view_id(focused)
        records: dict[int, WayfireWindowRecord] = {}
        for item in views if isinstance(views, list) else []:
            if not isinstance(item, Mapping):
                continue
            record = _record_from_view(
                item,
                matcher=_NullMatcher(),
                focused_id=focused_id,
            )
            if record is not None:
                records[record.id] = record
        self._records = records

    @staticmethod
    def _snapshot_for(record: WayfireWindowRecord) -> WindowSnapshot:
        return WindowSnapshot(
            id=record.window_id,
            desktop_id=record.desktop_id or "",
            title=record.title,
            app_id=record.app_id or None,
            active=record.active,
            minimized=record.minimized,
            fullscreen=record.fullscreen,
            geometry=record.geometry,
            workspace_id=record.workspace_id,
            can_activate=True,
            can_close=True,
        )


def load_wayfire_window_service(*, model, launcher) -> WayfireWindowService | None:
    """Return a Wayfire WindowService when the IPC socket is detectable."""
    socket_path = wayfire_socket_path()
    if socket_path is None or not socket_path.exists():
        return None
    return WayfireWindowService(
        model=model,
        launcher=launcher,
        client=WayfireIpcClient(socket_path=socket_path),
    )


def load_wayfire_workspace_service() -> WayfireWorkspaceService | None:
    """Return a Wayfire WorkspaceService when the IPC socket is detectable."""
    socket_path = wayfire_socket_path()
    if socket_path is None or not socket_path.exists():
        return None
    return WayfireWorkspaceService(client=WayfireIpcClient(socket_path=socket_path))


def load_wayfire_window_pick_service() -> WayfireWindowPickService | None:
    """Return a Wayfire WindowPickService when the IPC socket is detectable."""
    socket_path = wayfire_socket_path()
    if socket_path is None or not socket_path.exists():
        return None
    return WayfireWindowPickService(client=WayfireIpcClient(socket_path=socket_path))


def wayfire_socket_path(environ: Mapping[str, str] | None = None) -> Path | None:
    """Return the Wayfire IPC socket from env or the runtime-dir socket pattern."""
    env = os.environ if environ is None else environ
    raw = env.get("WAYFIRE_SOCKET", "").strip()
    if raw:
        return Path(raw)
    runtime_dir = env.get("XDG_RUNTIME_DIR", "").strip()
    if not runtime_dir:
        return None
    matches = sorted(Path(runtime_dir).glob("wayfire-*.socket"))
    if not matches:
        return None
    return matches[0]


def wayfire_ipc_available(environ: Mapping[str, str] | None = None) -> bool:
    """Return True when a Wayfire IPC socket is detectable."""
    path = wayfire_socket_path(environ)
    return path is not None and path.exists()


def _read_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RuntimeError("Wayfire IPC socket closed before response completed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _record_from_view(
    view: Mapping[str, Any],
    *,
    matcher: object,
    focused_id: int | None,
) -> WayfireWindowRecord | None:
    if not bool(view.get("mapped", True)):
        return None
    view_type = str(view.get("type", "") or "").strip().lower()
    role = str(view.get("role", "") or "").strip().lower()
    layer = str(view.get("layer", "") or "").strip().lower()
    if view_type != "toplevel" and role != "toplevel" and layer != "workspace":
        return None
    try:
        view_id = int(view.get("id"))
    except (TypeError, ValueError):
        return None
    app_id = str(view.get("app-id", "") or "").strip()
    desktop_id = matcher.match(app_id) if app_id else None
    wset_index = view.get("wset-index")
    workspace_id = str(wset_index) if wset_index not in (None, -1, "") else None
    return WayfireWindowRecord(
        id=view_id,
        title=str(view.get("title", "") or "Window"),
        app_id=app_id,
        desktop_id=desktop_id,
        active=bool(view.get("activated", False)) or view_id == focused_id,
        minimized=_optional_bool(view.get("minimized")),
        fullscreen=_optional_bool(view.get("fullscreen")),
        geometry=_rect_from_mapping(view.get("geometry")),
        workspace_id=workspace_id,
        pid=_optional_int(view.get("pid")),
    )


def _rect_from_mapping(value: object) -> Rect | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return Rect(
            x=int(value.get("x", 0)),
            y=int(value.get("y", 0)),
            width=int(value.get("width", 0)),
            height=int(value.get("height", 0)),
        )
    except (TypeError, ValueError):
        return None


def _workspace_records_from_output(
    output: Mapping[str, Any],
    *,
    number_offset: int,
    focused_output_id: int | None,
) -> list[WayfireWorkspaceRecord]:
    output_id = _optional_int(output.get("id"))
    workspace = output.get("workspace")
    if not isinstance(workspace, Mapping):
        return []
    try:
        grid_width = int(workspace.get("grid_width", 1))
        grid_height = int(workspace.get("grid_height", 1))
        active_x = int(workspace.get("x", 0))
        active_y = int(workspace.get("y", 0))
    except (TypeError, ValueError):
        return []
    output_name = str(output.get("name", "") or output_id or "output")
    records: list[WayfireWorkspaceRecord] = []
    for y in range(max(1, grid_height)):
        for x in range(max(1, grid_width)):
            number = number_offset + len(records) + 1
            record_id = f"{output_id or output_name}:{x},{y}"
            active = output_id == focused_output_id and x == active_x and y == active_y
            records.append(
                WayfireWorkspaceRecord(
                    id=record_id,
                    number=number,
                    name=f"{output_name} {x + 1},{y + 1}",
                    active=active,
                )
            )
    return records


def _focused_view_id(response: object) -> int | None:
    if not isinstance(response, Mapping):
        return None
    info = response.get("info")
    if not isinstance(info, Mapping):
        return None
    return _optional_int(info.get("id"))


def _focused_output_id(response: object) -> int | None:
    if not isinstance(response, Mapping):
        return None
    info = response.get("info")
    if not isinstance(info, Mapping):
        return None
    return _optional_int(info.get("id"))


class _NullMatcher:
    """Matcher used by picking, where desktop-id resolution is optional."""

    @staticmethod
    def match(_app_id: str) -> str | None:
        return None


def _wayfire_window_id(window_id: WindowId) -> int | None:
    if window_id.backend is not DisplayServer.WAYLAND:
        return None
    return _optional_int(window_id.value)


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    return None


def _optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
