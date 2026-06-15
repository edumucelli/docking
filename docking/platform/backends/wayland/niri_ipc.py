# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Niri IPC helpers and WindowService.

Niri exposes a single JSON Unix socket at ``$NIRI_SOCKET``.  Requests are
single-line JSON objects; replies are also single-line JSON.  The event
stream is started with an ``{"EventStream":null}`` request, acknowledged
with ``{"Ok":"Handled"}``, and then delivers full current state up-front
followed by incremental events — no polling needed.

The IPC is simpler than Hyprland's two-socket model and requires no
external tools.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from docking.log import get_logger
from docking.platform.backends.base import (
    ActionResult,
    DesktopActionService,
    DisplayServer,
    PreviewImage,
    PreviewService,
    Rect,
    ScreenCaptureService,
    WindowId,
    WindowService,
    WindowSnapshot,
    WorkspaceService,
    WorkspaceSnapshot,
)
from docking.platform.backends.wayland.toplevels import WaylandAppIdMatcher
from docking.platform.running import RunningAppInfo, RunningWindowInfo

log = get_logger(name="niri_ipc")

# ---------------------------------------------------------------------------
# Niri IPC wire helpers
# ---------------------------------------------------------------------------

_NIRI_WINDOW_EVENTS = {
    "WindowsChanged",
    "WindowOpenedOrChanged",
    "WindowClosed",
    "WindowFocusChanged",
    "WindowUrgencyChanged",
    "WorkspacesChanged",
    "WorkspaceActivated",
    "WorkspaceActiveWindowChanged",
}

_NIRI_WORKSPACE_EVENTS = {
    "WorkspacesChanged",
    "WorkspaceActivated",
    "WorkspaceActiveWindowChanged",
}


def _niri_socket_path(environ: Mapping[str, str] | None = None) -> Path | None:
    """Return the Niri IPC socket path from the process environment."""
    env = os.environ if environ is None else environ
    raw = env.get("NIRI_SOCKET", "").strip()
    if not raw:
        return None
    return Path(raw)


# ---------------------------------------------------------------------------
# Backend-neutral data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NiriWindowRecord:
    """Backend-neutral projection of one Niri window."""

    id: int
    title: str
    app_id: str
    desktop_id: str | None
    active: bool
    urgent: bool
    workspace_id: int | None
    geometry: Rect | None

    @property
    def window_id(self) -> WindowId:
        return WindowId(backend=DisplayServer.WAYLAND, value=str(self.id))


@dataclass(frozen=True)
class NiriWorkspaceRecord:
    """Backend-neutral projection of one Niri workspace."""

    id: int
    idx: int
    name: str
    output: str
    active: bool
    focused: bool
    number: int
    active_window_id: int | None = None

    @property
    def snapshot_id(self) -> str:
        return str(self.id)


# ---------------------------------------------------------------------------
# IPC request client
# ---------------------------------------------------------------------------


class NiriIpcClient:
    """Short-lived request client for Niri's JSON IPC socket."""

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

    def request(self, payload: object) -> dict[str, Any]:
        """Send one JSON request and return the parsed reply dict.

        Returns an empty dict on any error so callers can safely chain
        ``.get(...)`` accessors.
        """
        try:
            raw = json.dumps(payload, separators=(",", ":"))
        except (TypeError, ValueError):
            return {}
        try:
            with self._socket_factory(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self._timeout)
                sock.connect(self._path)
                sock.sendall((raw + "\n").encode("utf-8"))
                sock.shutdown(socket.SHUT_WR)
                chunks: list[bytes] = []
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
            return _parse_response(b"".join(chunks))
        except OSError as exc:
            log.info("Niri IPC request failed: %s", exc)
            return {}
        except json.JSONDecodeError as exc:
            log.info("Niri IPC response parse error: %s", exc)
            return {}

    def ok_data(self, payload: object, key: str) -> Any | None:
        """Send a request and return ``Ok.<key>`` from the reply, or None."""
        reply = self.request(payload)
        ok = reply.get("Ok")
        if isinstance(ok, Mapping):
            return ok.get(key)
        return None

    def action(self, action_payload: Mapping[str, Any]) -> ActionResult:
        """Send an ``Action`` request.  Returns OK when the server accepts it."""
        try:
            reply = self.request({"Action": action_payload})
            if "Ok" in reply:
                return ActionResult.OK
            return ActionResult.FAILED
        except Exception:
            return ActionResult.FAILED


# ---------------------------------------------------------------------------
# Event stream
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NiriEvent:
    """One parsed event from the Niri event stream."""

    name: str
    data: dict[str, Any]


class NiriEventStream:
    """Background reader for Niri's event stream.

    Sends ``{"EventStream":null}``, consumes the ``{"Ok":"Handled"}``
    acknowledgement, then reads line-by-line JSON events forever.
    """

    def __init__(
        self,
        *,
        socket_path: str | Path,
        callback: Callable[[NiriEvent], None],
        socket_factory: Callable[[int, int], socket.socket] = socket.socket,
        timeout: float = 1.0,
    ) -> None:
        self._path = str(socket_path)
        self._callback = callback
        self._socket_factory = socket_factory
        self._timeout = timeout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the reader thread once."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="docking-niri-events",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the reader thread."""
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self._timeout)
        self._thread = None

    def _run(self) -> None:
        try:
            with self._socket_factory(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self._timeout)
                sock.connect(self._path)
                # Start event stream.
                sock.sendall(b'{"EventStream":null}\n')
                sock.settimeout(None)
                file_obj = sock.makefile("rb")
                for raw_line in file_obj:
                    if self._stop.is_set():
                        break
                    event = _parse_event_line(raw_line.decode("utf-8", "replace"))
                    if event is not None:
                        self._callback(event)
        except OSError as exc:
            if not self._stop.is_set():
                log.info("Niri event stream stopped: %s", exc)


# ---------------------------------------------------------------------------
# WindowService
# ---------------------------------------------------------------------------


class NiriWindowService(WindowService):
    """WindowService backed by Niri IPC snapshots and events."""

    def __init__(
        self,
        *,
        model,
        launcher,
        client: NiriIpcClient,
        event_stream_factory: Callable[
            [Callable[[NiriEvent], None]], NiriEventStream | None
        ]
        | None = None,
    ) -> None:
        self._model = model
        self._matcher = WaylandAppIdMatcher(launcher=launcher)
        self._client = client
        self._event_stream_factory = event_stream_factory
        self._event_stream: NiriEventStream | None = None
        self._records: dict[int, NiriWindowRecord] = {}
        self._workspaces: dict[int, dict[str, Any]] = {}
        self._overview_open = False
        self._on_overview_changed: Callable[[bool], None] | None = None

    # -- WindowService interface -----------------------------------------------

    def start(self) -> None:
        """Load initial windows and subscribe to events."""
        self._refresh_windows()
        self._refresh_workspaces()
        if self._event_stream_factory is not None:
            self._event_stream = self._event_stream_factory(self._on_event)
            if self._event_stream is not None:
                self._event_stream.start()

    def stop(self) -> None:
        """Stop event delivery and clear published running state."""
        if self._event_stream is not None:
            self._event_stream.stop()
            self._event_stream = None
        self._records.clear()
        self._workspaces.clear()
        self._model.update_running(running={})

    def list_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        """Return known Niri windows for one desktop ID."""
        return tuple(
            self._snapshot_for(record)
            for record in self._records.values()
            if record.desktop_id == desktop_id
        )

    def list_preview_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        """Return menu rows; preview capture is not available via Niri IPC."""
        return self.list_windows(desktop_id=desktop_id)

    def icon_name_for_desktop(self, desktop_id: str) -> str:
        """Return a generic icon fallback."""
        return "application-x-executable"

    def protocol_handle_for_window_id(self, window_id: WindowId) -> object | None:
        """Niri IPC does not expose toplevel-export handles."""
        return None

    # -- Actions ----------------------------------------------------------------

    def activate(self, window_id: WindowId) -> ActionResult:
        """Focus a Niri window by id."""
        niri_id = _niri_window_id(window_id)
        if niri_id is None or niri_id not in self._records:
            return ActionResult.NOT_FOUND
        return self._client.action({"FocusWindow": {"id": niri_id}})

    def activate_most_recent(self, desktop_id: str) -> ActionResult:
        """Activate the active or first known window for an app."""
        record = self._first_record_for_desktop(desktop_id)
        if record is None:
            return ActionResult.NOT_FOUND
        return self.activate(record.window_id)

    def cycle(self, desktop_id: str) -> ActionResult:
        """Cycle policy starts as activating a known app window."""
        return self.activate_most_recent(desktop_id=desktop_id)

    def minimize_all(self, desktop_id: str) -> ActionResult:
        """Niri is a tiling compositor without a minimize concept."""
        return ActionResult.UNSUPPORTED

    def close(self, window_id: WindowId) -> ActionResult:
        """Close a Niri window by id."""
        niri_id = _niri_window_id(window_id)
        if niri_id is None or niri_id not in self._records:
            return ActionResult.NOT_FOUND
        return self._client.action({"CloseWindow": {"id": niri_id}})

    def close_all(self, desktop_id: str) -> ActionResult:
        """Close all known windows for an app."""
        records = self._records_for_desktop(desktop_id)
        if not records:
            return ActionResult.NOT_FOUND
        result = ActionResult.OK
        for record in records:
            action = self._client.action({"CloseWindow": {"id": record.id}})
            if action is not ActionResult.OK:
                result = action
        return result

    def close_focused(self, desktop_id: str) -> ActionResult:
        """Close the active window for an app, falling back to the first."""
        record = self._first_record_for_desktop(desktop_id)
        if record is None:
            return ActionResult.NOT_FOUND
        return self.close(record.window_id)

    def toggle_focus(self, desktop_id: str) -> ActionResult:
        """Focus inactive apps; no-op for active ones (no minimize in Niri)."""
        record = self._first_record_for_desktop(desktop_id)
        if record is None:
            return ActionResult.NOT_FOUND
        if record.active:
            return ActionResult.OK  # already focused; nothing to toggle
        return self.activate(record.window_id)

    def fullscreen(self, window_id: WindowId) -> ActionResult:
        """Toggle fullscreen state for a Niri window."""
        niri_id = _niri_window_id(window_id)
        if niri_id is None or niri_id not in self._records:
            return ActionResult.NOT_FOUND
        return self._client.action({"FullscreenWindow": {"id": niri_id}})

    @property
    def is_overview_open(self) -> bool:
        """Return True when the Niri overview UI is currently open."""
        return self._overview_open

    def set_overview_changed_callback(
        self, callback: Callable[[bool], None] | None
    ) -> None:
        """Register a callback invoked when the overview opens or closes.

        The callback receives ``True`` when the overview opens and ``False``
        when it closes.
        """
        self._on_overview_changed = callback

    # -- Internal ---------------------------------------------------------------

    def _on_event(self, event: NiriEvent) -> None:
        if event.name in _NIRI_WINDOW_EVENTS:
            # Full state replacements.
            if event.name == "WindowsChanged":
                windows_raw = event.data.get("windows")
                if isinstance(windows_raw, list):
                    self._replace_windows(windows_raw)
                return
            if event.name == "WorkspacesChanged":
                workspaces_raw = event.data.get("workspaces")
                if isinstance(workspaces_raw, list):
                    self._workspaces = {
                        ws["id"]: ws
                        for ws in workspaces_raw
                        if isinstance(ws, Mapping) and "id" in ws
                    }
                return
            # Incremental updates.
            if event.name == "WindowOpenedOrChanged":
                window_raw = event.data.get("window")
                if isinstance(window_raw, Mapping):
                    self._upsert_window(window_raw)
            elif event.name == "WindowClosed":
                window_id = event.data.get("id")
                if isinstance(window_id, int):
                    self._records.pop(window_id, None)
            elif event.name == "WindowFocusChanged":
                self._apply_focus_change(event.data.get("id"))
            elif event.name == "WindowUrgencyChanged":
                self._apply_urgency_change(event.data)
            elif event.name == "WorkspaceActivated":
                pass  # workspace metadata update, not window state
            elif event.name == "WorkspaceActiveWindowChanged":
                pass  # workspace-level change, not window state
            elif event.name == "OverviewOpenedOrClosed":
                is_open = event.data.get("is_open")
                if isinstance(is_open, bool) and is_open != self._overview_open:
                    self._overview_open = is_open
                    if self._on_overview_changed is not None:
                        self._on_overview_changed(is_open)
                return  # no window state change
            self._publish_running()

    def _refresh_windows(self) -> None:
        windows_raw = self._client.ok_data({"Windows": None}, "Windows")
        if isinstance(windows_raw, list):
            self._replace_windows(windows_raw)
            self._publish_running()

    def _refresh_workspaces(self) -> None:
        workspaces_raw = self._client.ok_data({"Workspaces": None}, "Workspaces")
        if isinstance(workspaces_raw, list):
            self._workspaces = {
                ws["id"]: ws
                for ws in workspaces_raw
                if isinstance(ws, Mapping) and "id" in ws
            }

    def _replace_windows(self, items: list[Any]) -> None:
        self._matcher.sync_visible_items(self._model.visible_items())
        records: dict[int, NiriWindowRecord] = {}
        for item in items:
            if not isinstance(item, Mapping):
                continue
            record = _record_from_window(item, matcher=self._matcher)
            if record is not None:
                records[record.id] = record
        self._records = records

    def _upsert_window(self, item: Mapping[str, Any]) -> None:
        record = _record_from_window(item, matcher=self._matcher)
        if record is not None:
            self._records[record.id] = record

    def _apply_focus_change(self, focused_id: object) -> None:
        if not isinstance(focused_id, int | type(None)):
            return
        for record in self._records.values():
            record = self._records[record.id]
            active = record.id == focused_id
            if record.active != active:
                self._records[record.id] = NiriWindowRecord(
                    id=record.id,
                    title=record.title,
                    app_id=record.app_id,
                    desktop_id=record.desktop_id,
                    active=active,
                    urgent=record.urgent,
                    workspace_id=record.workspace_id,
                    geometry=record.geometry,
                )

    def _apply_urgency_change(self, data: Mapping[str, Any]) -> None:
        window_id = data.get("id")
        urgent = data.get("urgent")
        if not isinstance(window_id, int) or not isinstance(urgent, bool):
            return
        record = self._records.get(window_id)
        if record is None or record.urgent == urgent:
            return
        self._records[window_id] = NiriWindowRecord(
            id=record.id,
            title=record.title,
            app_id=record.app_id,
            desktop_id=record.desktop_id,
            active=record.active,
            urgent=urgent,
            workspace_id=record.workspace_id,
            geometry=record.geometry,
        )

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
                    urgent=record.urgent,
                    window=str(record.id),
                )
            )
        self._model.update_running(
            running={
                desktop_id: RunningAppInfo.from_windows(items)
                for desktop_id, items in windows_by_desktop.items()
            }
        )

    def _snapshot_for(self, record: NiriWindowRecord) -> WindowSnapshot:
        return WindowSnapshot(
            id=record.window_id,
            desktop_id=record.desktop_id or "",
            title=record.title,
            app_id=record.app_id or None,
            active=record.active,
            urgent=record.urgent,
            minimized=None,
            fullscreen=None,
            geometry=record.geometry,
            workspace_id=str(record.workspace_id)
            if record.workspace_id is not None
            else None,
            can_activate=True,
            can_minimize=False,  # Niri is a tiling compositor
            can_close=True,
            can_preview=True,  # ScreenshotWindow IPC
        )

    def _records_for_desktop(self, desktop_id: str) -> list[NiriWindowRecord]:
        return [
            record
            for record in self._records.values()
            if record.desktop_id == desktop_id
        ]

    def _first_record_for_desktop(self, desktop_id: str) -> NiriWindowRecord | None:
        records = self._records_for_desktop(desktop_id)
        if not records:
            return None
        return next((r for r in records if r.active), records[0])


class NiriWorkspaceService(WorkspaceService):
    """WorkspaceService backed by Niri IPC workspace snapshots and actions."""

    def __init__(
        self,
        *,
        client: NiriIpcClient,
        event_stream_factory: Callable[
            [Callable[[NiriEvent], None]], NiriEventStream | None
        ]
        | None = None,
    ) -> None:
        self._client = client
        self._event_stream_factory = event_stream_factory
        self._event_stream: NiriEventStream | None = None
        self._records: dict[str, NiriWorkspaceRecord] = {}
        self._watchers: dict[int, Callable[[], None]] = {}
        self._next_watch_id = 1
        self._active_ids: tuple[str, ...] = ()

    def start(self) -> None:
        self._refresh_workspaces()
        if self._event_stream_factory is not None:
            self._event_stream = self._event_stream_factory(self._on_event)
            if self._event_stream is not None:
                self._event_stream.start()

    def stop(self) -> None:
        if self._event_stream is not None:
            self._event_stream.stop()
            self._event_stream = None
        self._records.clear()
        self._watchers.clear()
        self._active_ids = ()

    def list_workspaces(self) -> Sequence[WorkspaceSnapshot]:
        return tuple(
            WorkspaceSnapshot(
                id=record.snapshot_id,
                number=record.number,
                name=record.name,
                active=record.focused,
            )
            for record in sorted(self._records.values(), key=_workspace_sort_key)
        )

    def active_workspace(self) -> WorkspaceSnapshot | None:
        for workspace in self.list_workspaces():
            if workspace.active:
                return workspace
        return None

    def activate(self, workspace_id: str) -> ActionResult:
        record = self._resolve_workspace(workspace_id)
        if record is None:
            return ActionResult.NOT_FOUND
        return _focus_niri_workspace(client=self._client, record=record)

    def watch_active_workspace(self, on_change: Callable[[], None]) -> object | None:
        watch_id = self._next_watch_id
        self._next_watch_id += 1
        self._watchers[watch_id] = on_change
        return watch_id

    def unwatch_active_workspace(self, handle: object) -> None:
        if isinstance(handle, int):
            self._watchers.pop(handle, None)

    def _on_event(self, event: NiriEvent) -> None:
        if event.name not in _NIRI_WORKSPACE_EVENTS:
            return
        if event.name == "WorkspacesChanged":
            workspaces_raw = event.data.get("workspaces")
            if isinstance(workspaces_raw, list):
                self._replace_workspaces(workspaces_raw)
            return
        self._refresh_workspaces()

    def _refresh_workspaces(self) -> None:
        workspaces_raw = self._client.ok_data({"Workspaces": None}, "Workspaces")
        if isinstance(workspaces_raw, list):
            self._replace_workspaces(workspaces_raw)

    def _replace_workspaces(self, items: list[Any]) -> None:
        records: list[NiriWorkspaceRecord] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            record = _record_from_workspace(item, number=len(records))
            if record is not None:
                records.append(record)
        self._records = {record.snapshot_id: record for record in records}
        self._notify_if_active_changed()

    def _notify_if_active_changed(self) -> None:
        active_ids = tuple(
            record.snapshot_id
            for record in sorted(self._records.values(), key=_workspace_sort_key)
            if record.focused
        )
        if active_ids == self._active_ids:
            return
        self._active_ids = active_ids
        for callback in tuple(self._watchers.values()):
            callback()

    def _resolve_workspace(self, workspace_id: str) -> NiriWorkspaceRecord | None:
        record = self._records.get(workspace_id)
        if record is not None:
            return record
        for candidate in self._records.values():
            if str(candidate.number) == workspace_id or candidate.name == workspace_id:
                return candidate
        return None


class NiriDesktopActionService(DesktopActionService):
    """DesktopActionService that focuses an empty Niri workspace."""

    def __init__(self, *, client: NiriIpcClient) -> None:
        self._client = client
        self._restore_by_output: dict[str, str] = {}

    def start(self) -> None:
        """No persistent runtime resources."""

    def stop(self) -> None:
        self._restore_by_output.clear()

    def show_desktop(self, show: bool | None = None) -> ActionResult:
        records = self._list_workspaces()
        focused = next((record for record in records if record.focused), None)
        if focused is None:
            return ActionResult.NOT_FOUND

        wants_show = focused.active_window_id is not None if show is None else show
        if wants_show:
            if focused.active_window_id is not None:
                self._restore_by_output[focused.output] = focused.snapshot_id
            empty = self._empty_workspace_for_output(
                records=records,
                output=focused.output,
                exclude_id=focused.snapshot_id,
            )
            if empty is None:
                return ActionResult.NOT_FOUND
            return _focus_niri_workspace(client=self._client, record=empty)

        restore_id = self._restore_by_output.pop(focused.output, "")
        restore = next(
            (record for record in records if record.snapshot_id == restore_id),
            None,
        )
        if restore is None:
            return ActionResult.OK
        return _focus_niri_workspace(client=self._client, record=restore)

    def _list_workspaces(self) -> tuple[NiriWorkspaceRecord, ...]:
        workspaces_raw = self._client.ok_data({"Workspaces": None}, "Workspaces")
        if not isinstance(workspaces_raw, list):
            return ()
        records: list[NiriWorkspaceRecord] = []
        for item in workspaces_raw:
            if not isinstance(item, Mapping):
                continue
            record = _record_from_workspace(item, number=len(records))
            if record is not None:
                records.append(record)
        return tuple(records)

    def _empty_workspace_for_output(
        self,
        *,
        records: Sequence[NiriWorkspaceRecord],
        output: str,
        exclude_id: str,
    ) -> NiriWorkspaceRecord | None:
        for record in records:
            if record.output != output or record.snapshot_id == exclude_id:
                continue
            if record.active_window_id is None:
                return record
        return None


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------


def load_niri_window_service(*, model, launcher) -> NiriWindowService | None:
    """Return a Niri WindowService when the IPC socket is detectable."""
    socket_path = _niri_socket_path()
    if socket_path is None or not socket_path.exists():
        return None
    client = NiriIpcClient(socket_path=socket_path)
    return NiriWindowService(
        model=model,
        launcher=launcher,
        client=client,
        event_stream_factory=lambda callback: NiriEventStream(
            socket_path=socket_path,
            callback=callback,
        ),
    )


def load_niri_workspace_service() -> NiriWorkspaceService | None:
    """Return a Niri WorkspaceService when the IPC socket is detectable."""
    socket_path = _niri_socket_path()
    if socket_path is None or not socket_path.exists():
        return None
    client = NiriIpcClient(socket_path=socket_path)
    return NiriWorkspaceService(
        client=client,
        event_stream_factory=lambda callback: NiriEventStream(
            socket_path=socket_path,
            callback=callback,
        ),
    )


def load_niri_desktop_action_service() -> NiriDesktopActionService | None:
    """Return Niri desktop actions when the IPC socket is detectable."""
    socket_path = _niri_socket_path()
    if socket_path is None or not socket_path.exists():
        return None
    return NiriDesktopActionService(client=NiriIpcClient(socket_path=socket_path))


def load_niri_preview_service() -> NiriPreviewService | None:
    """Return a Niri PreviewService when the IPC socket is detectable."""
    socket_path = _niri_socket_path()
    if socket_path is None or not socket_path.exists():
        return None
    return NiriPreviewService(socket_path=str(socket_path))


def niri_pick_color(
    socket_path: str | None = None, *, timeout: float = 10.0
) -> tuple[int, int, int] | None:
    """Pick a screen colour via Niri's ``PickColor`` IPC request.

    Returns an ``(r, g, b)`` tuple of byte values (0-255), or ``None`` when
    the picker is unavailable or the user cancels.
    """
    path = socket_path or _niri_socket_path()
    if path is None:
        return None
    try:
        raw = b'{"PickColor":null}\n'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(path))
            sock.sendall(raw)
            sock.shutdown(socket.SHUT_WR)
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        reply = _parse_response(b"".join(chunks))
        ok = reply.get("Ok")
        if isinstance(ok, Mapping):
            picked = ok.get("PickedColor")
            if isinstance(picked, Mapping):
                rgb = picked.get("rgb")
                if isinstance(rgb, list) and len(rgb) == 3:
                    return (
                        max(0, min(255, round(float(rgb[0]) * 255))),
                        max(0, min(255, round(float(rgb[1]) * 255))),
                        max(0, min(255, round(float(rgb[2]) * 255))),
                    )
        return None
    except OSError as exc:
        log.info("Niri PickColor failed: %s", exc)
        return None


class NiriScreenCaptureService(ScreenCaptureService):
    """ScreenCaptureService using Niri's native ``PickColor`` IPC."""

    def __init__(self, socket_path: str, *, timeout: float = 10.0) -> None:
        self._socket_path = socket_path
        self._timeout = timeout

    def start(self) -> None:
        """No persistent state."""

    def stop(self) -> None:
        """No persistent state."""

    def pick_color(self, *, x: int, y: int) -> tuple[int, int, int] | None:
        del x, y  # Niri's interactive picker ignores coordinates.
        return niri_pick_color(socket_path=self._socket_path, timeout=self._timeout)


# ---------------------------------------------------------------------------
# Preview service
# ---------------------------------------------------------------------------


class NiriPreviewService(PreviewService):
    """PreviewService backed by Niri's ``ScreenshotWindow`` IPC action.

    Each capture opens a short-lived connection to ``$NIRI_SOCKET``, sends a
    ``ScreenshotWindow`` action with a temporary file path, reads the
    resulting PNG from disk, and returns a scaled :class:`PreviewImage`.
    """

    def __init__(self, *, socket_path: str, timeout: float = 2.0) -> None:
        self._socket_path = socket_path
        self._timeout = timeout

    # -- Service ---------------------------------------------------------------

    def start(self) -> None:
        """No persistent state to initialise."""

    def stop(self) -> None:
        """No persistent state to tear down."""

    # -- PreviewService --------------------------------------------------------

    def capture(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        return self._capture_impl(window_id, width=width, height=height)

    def thumbnail(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        return self._capture_impl(window_id, width=width, height=height)

    # -- Internal ---------------------------------------------------------------

    def _capture_impl(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        if window_id.backend is not DisplayServer.WAYLAND:
            return None
        niri_id = _niri_window_id(window_id)
        if niri_id is None:
            return None

        tmp_path = None
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(
                suffix=".png", prefix="docking-niri-preview-"
            )
            os.close(tmp_fd)

            payload = {
                "Action": {
                    "ScreenshotWindow": {
                        "id": niri_id,
                        "write_to_disk": True,
                        "show_pointer": False,
                        "path": tmp_path,
                    }
                }
            }
            raw = json.dumps(payload, separators=(",", ":"))
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self._timeout)
                sock.connect(self._socket_path)
                sock.sendall((raw + "\n").encode("utf-8"))
                sock.shutdown(socket.SHUT_WR)
                # Drain the response (screenshot is async; the file
                # won't be ready until the compositor finishes writing).
                _drain_socket(sock)

            # Wait for the compositor to write the screenshot file.
            if not _wait_for_nonempty_file(tmp_path, timeout=self._timeout):
                return None

            # Load the PNG from disk.
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(tmp_path)
            if pixbuf is None:
                return None

            scaled = pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
            image = scaled if scaled is not None else pixbuf
            return PreviewImage(
                image=image,
                width=int(image.get_width()),
                height=int(image.get_height()),
            )
        except OSError as exc:
            log.info("Niri preview capture failed for window %s: %s", niri_id, exc)
            return None
        finally:
            if tmp_path is not None:
                with suppress(OSError):
                    Path(tmp_path).unlink()


def _drain_socket(sock: socket.socket) -> None:
    """Read and discard any remaining data from *sock*."""
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
    except OSError:
        pass


def _wait_for_nonempty_file(path: str, *, timeout: float = 2.0) -> bool:
    """Poll *path* until it exists and has non-zero size, or *timeout* expires."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            st = Path(path).stat()
            if st.st_size > 0:
                return True
        except OSError:
            pass
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# Parse helpers — internal
# ---------------------------------------------------------------------------


def _parse_response(raw: bytes) -> dict[str, Any]:
    """Parse one reply line from Niri IPC into a dict."""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    return json.loads(text)  # type: ignore[no-any-return]


def _parse_event_line(line: str) -> NiriEvent | None:
    """Parse one event-stream line into a ``NiriEvent``."""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, Mapping) or len(obj) != 1:
        return None
    # The first ``{"Ok":"Handled"}`` acknowledgement is not a real event.
    if "Ok" in obj:
        return None
    (name, data_raw) = next(iter(obj.items()))
    if not isinstance(name, str):
        return None
    data = dict(data_raw) if isinstance(data_raw, Mapping) else {}
    return NiriEvent(name=name, data=data)


def _record_from_window(
    item: Mapping[str, Any],
    *,
    matcher: WaylandAppIdMatcher,
) -> NiriWindowRecord | None:
    window_id = item.get("id")
    if not isinstance(window_id, int):
        return None
    app_id = str(item.get("app_id") or "").strip()
    desktop_id = matcher.match(app_id) if app_id else None
    workspace_id = item.get("workspace_id")
    if not isinstance(workspace_id, int):
        workspace_id = None
    geometry = _geometry_from_niri_window(item)
    return NiriWindowRecord(
        id=window_id,
        title=str(item.get("title") or "Window"),
        app_id=app_id,
        desktop_id=desktop_id,
        active=bool(item.get("is_focused", False)),
        urgent=bool(item.get("is_urgent", False)),
        workspace_id=workspace_id,
        geometry=geometry,
    )


def _record_from_workspace(
    item: Mapping[str, Any],
    *,
    number: int,
) -> NiriWorkspaceRecord | None:
    workspace_id = item.get("id")
    idx = item.get("idx")
    if not isinstance(workspace_id, int) or not isinstance(idx, int):
        return None
    name = str(item.get("name") or "").strip()
    output = str(item.get("output") or "").strip()
    active_window_id = item.get("active_window_id")
    if not isinstance(active_window_id, int):
        active_window_id = None
    return NiriWorkspaceRecord(
        id=workspace_id,
        idx=idx,
        name=name,
        output=output,
        active=bool(item.get("is_active", False)),
        focused=bool(item.get("is_focused", False)),
        number=number,
        active_window_id=active_window_id,
    )


def _workspace_sort_key(record: NiriWorkspaceRecord) -> tuple[int, int]:
    return (record.number, record.id)


def _focus_niri_workspace(
    *,
    client: NiriIpcClient,
    record: NiriWorkspaceRecord,
) -> ActionResult:
    if record.output:
        monitor = client.action({"FocusMonitor": {"output": record.output}})
        if monitor is not ActionResult.OK:
            return monitor
    return client.action({"FocusWorkspace": {"reference": {"Index": record.idx}}})


def _geometry_from_niri_window(item: Mapping[str, Any]) -> Rect | None:
    """Extract window geometry from Niri's layout fields.

    Niri windows don't have a single global ``(x, y, w, h)`` since they're
    tiled.  We derive an approximate geometry from the tiling layout:
    ``tile_pos_in_workspace_view`` + ``window_size`` when available.
    """
    layout = item.get("layout")
    if not isinstance(layout, Mapping):
        return None
    size = layout.get("window_size")
    if not isinstance(size, Sequence) or isinstance(size, str | bytes) or len(size) < 2:
        return None
    try:
        w = int(size[0])
        h = int(size[1])
    except (TypeError, ValueError):
        return None
    pos = layout.get("tile_pos_in_workspace_view")
    if isinstance(pos, Sequence) and not isinstance(pos, str | bytes) and len(pos) >= 2:
        try:
            x = int(pos[0])
            y = int(pos[1])
        except (TypeError, ValueError):
            x, y = 0, 0
    else:
        x, y = 0, 0
    return Rect(x=x, y=y, width=w, height=h)


def _niri_window_id(window_id: WindowId) -> int | None:
    """Extract a Niri numeric window id from a backend-neutral ``WindowId``."""
    if window_id.backend is not DisplayServer.WAYLAND:
        return None
    try:
        return int(window_id.value)
    except (TypeError, ValueError):
        return None
