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

Snapshots are reconciled with IPC events. View rectangles are output-relative;
the service projects them into global coordinates and keeps workspace cells
distinct from the workspace sets that contain them.
"""

from __future__ import annotations

import json
import math
import os
import socket
import struct
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any, cast

from docking.core.json_types import JsonObject, JsonValue, as_json_object
from docking.log import get_logger
from docking.platform.applications.matcher import AppIdMatcher
from docking.platform.applications.running import RunningAppInfo, RunningWindowInfo
from docking.platform.applications.types import (
    ApplicationMatch,
)
from docking.platform.backends.base import (
    ActionResult,
    DesktopActionService,
    DisplayServer,
    Rect,
    VisibilityMonitor,
    VisibilityService,
    WindowId,
    WindowPickService,
    WindowService,
    WindowSnapshot,
    WorkspaceService,
    WorkspaceSnapshot,
)

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry

log = get_logger(name="wayfire_ipc")

try:
    from gi.repository import GLib
except (ImportError, ValueError):
    GLib = cast(Any, None)

WAYFIRE_WINDOW_POLL_SECONDS = 2
_WAYFIRE_EVENTS = (
    "view-mapped",
    "view-unmapped",
    "view-title-changed",
    "view-app-id-changed",
    "view-minimized",
    "view-fullscreen",
    "view-focused",
    "view-workspace-changed",
    "view-geometry-changed",
    "view-tiled",
    "view-sticky",
    "view-set-output",
    "view-wset-changed",
    "output-gain-focus",
    "output-wset-changed",
    "wset-workspace-changed",
)
_MAX_IPC_MESSAGE_BYTES = 16 * 1024 * 1024


class WayfireEventWatcher:
    """Persistent socket watcher that reads Wayfire IPC events in a background
    thread and dispatches them through a bounded GLib timer."""

    def __init__(
        self,
        *,
        socket_path: str,
        on_event: Callable[[str, Mapping[str, JsonValue]], None],
        socket_factory: Callable[[int, int], socket.socket] = socket.socket,
    ) -> None:
        self._path = socket_path
        self._on_event = on_event
        self._socket_factory = socket_factory
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._queue: Queue[JsonObject] = Queue()
        self._glib_source_id = 0

    def start(self) -> bool:
        """Subscribe to events and start the reader thread.

        Returns True when the subscription succeeded and the thread is running.
        """
        if self._running:
            return True
        self.stop()
        sock = None
        try:
            sock = self._socket_factory(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(self._path)
            payload = json.dumps(
                {
                    "method": "window-rules/events/watch",
                    "data": {"events": list(_WAYFIRE_EVENTS)},
                },
                separators=(",", ":"),
            ).encode("utf-8")
            sock.sendall(struct.pack("I", len(payload)) + payload)
            response_text = _read_message(sock).decode("utf-8")
            response = as_json_object(cast(JsonValue, json.loads(response_text)))
            if not isinstance(response, Mapping) or response.get("result") != "ok":
                log.info("Wayfire event subscription rejected: %s", response)
                sock.close()
                return False
        except (OSError, ValueError, RuntimeError) as exc:
            log.info("Wayfire event subscription failed: %s", exc)
            if sock is not None:
                sock.close()
            return False

        self._sock = sock
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        if GLib is not None:
            # A repeating idle callback runs continuously even with no events.
            self._glib_source_id = GLib.timeout_add(50, self._drain_queue)
        return True

    @property
    def running(self) -> bool:
        return self._running

    def stop(self) -> None:
        self._running = False
        sock = self._sock
        if sock is not None:
            with suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                sock.close()
            self._sock = None
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None
        if GLib is not None and self._glib_source_id:
            GLib.source_remove(self._glib_source_id)
            self._glib_source_id = 0
        self._queue = Queue()

    def _read_loop(self) -> None:
        sock = self._sock
        pending = bytearray()
        try:
            while self._running and sock is not None:
                try:
                    chunk = sock.recv(65536)
                except TimeoutError:
                    # Keep partial headers/payloads across idle timeouts.
                    continue
                if not chunk:
                    break
                pending.extend(chunk)
                while len(pending) >= 4:
                    size = struct.unpack("I", pending[:4])[0]
                    if size > _MAX_IPC_MESSAGE_BYTES:
                        raise RuntimeError("Wayfire event frame exceeds size limit")
                    if len(pending) < size + 4:
                        break
                    payload = bytes(pending[4 : size + 4])
                    del pending[: size + 4]
                    try:
                        parsed = as_json_object(
                            cast(JsonValue, json.loads(payload.decode("utf-8")))
                        )
                    except ValueError as exc:
                        log.debug("Wayfire event parse error: %s", exc)
                        continue
                    if parsed is not None:
                        self._queue.put(parsed)
        except (OSError, RuntimeError) as exc:
            if self._running:
                log.debug("Wayfire event stream disconnected: %s", exc)
        finally:
            self._running = False
            if sock is not None:
                with suppress(OSError):
                    sock.close()

    def _drain_queue(self) -> bool:
        handled = 0
        while handled < 20:  # bound per idle callback to avoid starving the loop
            try:
                event = self._queue.get_nowait()
            except Empty:
                break
            event_name = event.get("event", "")
            view_data = event.get("view")
            view = as_json_object(view_data)
            if isinstance(event_name, str):
                # Output/workspace events and focus loss do not contain a view.
                self._on_event(event_name, view if view is not None else {})
            handled += 1
        if not self._running and self._queue.empty():
            self._glib_source_id = 0
            return False
        return True


@dataclass(frozen=True)
class WayfireWindowRecord:
    """Backend-neutral projection of one Wayfire view."""

    id: int
    title: str
    app_id: str
    application_match: ApplicationMatch | None
    active: bool
    minimized: bool | None
    fullscreen: bool | None
    geometry: Rect | None
    workspace_id: str | None
    pid: int | None
    output_id: int | None = None
    wset_index: int | None = None
    maximized: bool | None = None
    sticky: bool = False
    last_focus_timestamp: int = 0

    @property
    def window_id(self) -> WindowId:
        return WindowId(backend=DisplayServer.WAYLAND, value=str(self.id))

    @property
    def desktop_id(self) -> str | None:
        return (
            self.application_match.desktop_id
            if self.application_match is not None
            else None
        )


@dataclass(frozen=True)
class WayfireWorkspaceRecord:
    """One logical workspace cell from a Wayfire workspace set."""

    id: str
    number: int
    name: str
    active: bool
    output_id: int
    grid_x: int
    grid_y: int


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
        self._methods: frozenset[str] | None = None
        self._methods_checked = False

    def request(
        self, method: str, data: Mapping[str, JsonValue] | None = None
    ) -> JsonValue:
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
            response = _read_message(sock)
        return cast(JsonValue, json.loads(response.decode("utf-8")))

    def supports_method(self, method: str) -> bool:
        """Check the loaded plugins without executing a destructive action.

        Older servers without method enumeration retain their action fallback.
        """
        if not self._methods_checked:
            self._methods_checked = True
            try:
                response = self.request("list-methods")
            except (OSError, ValueError, RuntimeError):
                response = None
            methods = response.get("methods") if isinstance(response, Mapping) else None
            if isinstance(methods, list):
                self._methods = frozenset(
                    item for item in methods if isinstance(item, str)
                )
        return self._methods is None or method in self._methods

    def action(self, method: str, data: Mapping[str, JsonValue]) -> ActionResult:
        """Call an action method and map Wayfire's result/error to ActionResult."""
        if not self.supports_method(method):
            return ActionResult.UNSUPPORTED
        try:
            response = self.request(method, data)
        except (OSError, ValueError, RuntimeError) as exc:
            log.info("Wayfire IPC action failed: %s", exc)
            return ActionResult.FAILED
        if isinstance(response, Mapping) and response.get("result") == "ok":
            return ActionResult.OK
        if isinstance(response, Mapping) and "error" in response:
            error = str(response.get("error")).lower()
            if "no such method" in error or "method not found" in error:
                return ActionResult.UNSUPPORTED
            if "not found" in error or "no such view" in error:
                return ActionResult.NOT_FOUND
        return ActionResult.FAILED


class WayfireWindowService(WindowService):
    """WindowService backed by Wayfire ``ipc-rules`` snapshots.

    Uses ``window-rules/events/watch`` for near-instant updates when
    available, falling back to periodic polling otherwise.
    """

    def __init__(
        self,
        *,
        model,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
        client: WayfireIpcClient,
        watcher: WayfireEventWatcher | None = None,
        config: Config | None = None,
    ) -> None:
        self._model = model
        self._matcher = AppIdMatcher(
            registry=application_registry,
            process_identity_service=process_identity_service,
        )
        self._client = client
        self._config = config
        self._records: dict[int, WayfireWindowRecord] = {}
        self._outputs: dict[int, Mapping[str, JsonValue]] = {}
        self._poll_source_id = 0
        self._watcher = watcher
        self._watcher_was_running = False
        self._watcher_retry_after = 0.0

    def start(self) -> None:
        if self._watcher is not None:
            self._watcher_was_running = self._watcher.start()
            self._watcher_retry_after = 0.0
        self._refresh()
        # The timer also reconciles dropped events and retries disconnected
        # subscriptions. In particular, the snapshot predates queued events.
        if GLib is not None and self._poll_source_id == 0:
            self._poll_source_id = GLib.timeout_add_seconds(
                WAYFIRE_WINDOW_POLL_SECONDS,
                self._poll,
            )

    def stop(self) -> None:
        if self._watcher is not None:
            self._watcher.stop()
        if GLib is not None and self._poll_source_id:
            GLib.source_remove(self._poll_source_id)
        self._poll_source_id = 0
        self._watcher_was_running = False
        self._watcher_retry_after = 0.0
        self._records.clear()
        self._outputs.clear()
        self._model.update_running(running={})

    def list_all_windows(self) -> Sequence[WindowSnapshot]:
        return tuple(self._snapshot_for(record) for record in self._records.values())

    def list_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        return tuple(
            self._snapshot_for(record)
            for record in self._records.values()
            if record.desktop_id == desktop_id and self._in_scope(record)
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
        records = self._records_for_desktop(desktop_id)
        if not records:
            return ActionResult.NOT_FOUND
        # Keep creation order for cycling: sorting by focus time would reorder
        # this ring after every activation and strand a third window.
        active_index = next((i for i, row in enumerate(records) if row.active), -1)
        return self.activate(records[(active_index + 1) % len(records)].window_id)

    @property
    def supports_minimize(self) -> bool:
        return self._client.supports_method("wm-actions/set-minimized")

    def minimize_all(self, desktop_id: str) -> ActionResult:
        records = self._records_for_desktop(desktop_id)
        if not records:
            return ActionResult.NOT_FOUND
        if not self.supports_minimize:
            return ActionResult.UNSUPPORTED
        result = ActionResult.OK
        for record in records:
            action = self._client.action(
                "wm-actions/set-minimized",
                {"view_id": record.id, "state": True},
            )
            if action is not ActionResult.OK:
                result = action
        if result is ActionResult.OK:
            self._refresh()
        return result

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
        record = next(
            (row for row in self._records_for_desktop(desktop_id) if row.active), None
        )
        if record is None:
            return ActionResult.NOT_FOUND
        return self.close(record.window_id)

    def toggle_focus(self, desktop_id: str) -> ActionResult:
        record = self._first_record_for_desktop(desktop_id)
        if record is None:
            return ActionResult.NOT_FOUND
        if record.active:
            if not self.supports_minimize:
                return ActionResult.UNSUPPORTED
            result = self._client.action(
                "wm-actions/set-minimized",
                {"view_id": record.id, "state": True},
            )
            if result is ActionResult.OK:
                self._refresh()
            return result
        return self.activate(record.window_id)

    def _refresh(self) -> None:
        try:
            views = self._client.request("window-rules/list-views")
            focused = self._client.request("window-rules/get-focused-view")
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            log.info("Wayfire IPC snapshot unavailable: %s", exc)
            return
        self._outputs = _load_outputs(self._client)
        focused_id = _focused_view_id(focused)
        if not isinstance(views, list):
            return
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
            if record.desktop_id is None or not self._in_scope(record):
                continue
            windows_by_desktop.setdefault(record.desktop_id, []).append(
                RunningWindowInfo(
                    desktop_id=record.desktop_id,
                    xid=0,
                    window_id=record.window_id,
                    active=record.active,
                    urgent=False,
                    window=str(record.id),
                    runtime_app=(
                        record.application_match.runtime_app
                        if record.application_match is not None
                        else None
                    ),
                )
            )
        self._model.update_running(
            running={
                desktop_id: RunningAppInfo.from_windows(items)
                for desktop_id, items in windows_by_desktop.items()
            }
        )

    def _poll(self) -> bool:
        if (
            self._watcher is not None
            and self._watcher_was_running
            and not self._watcher.running
            and time.monotonic() >= self._watcher_retry_after
        ):
            # An initially rejected subscription keeps its polling fallback.
            # Established streams can reconnect, with a cooldown on failure.
            self._watcher_retry_after = (
                0.0 if self._watcher.start() else time.monotonic() + 60.0
            )
        self._refresh()
        return True

    def _on_event(self, event_name: str, view: Mapping[str, JsonValue]) -> None:
        """Handle one Wayfire window event by updating the local record set."""
        if event_name in (
            "view-set-output",
            "view-wset-changed",
            "output-gain-focus",
            "output-wset-changed",
            "wset-workspace-changed",
        ):
            self._refresh()
            return
        if event_name == "view-focused" and not view:
            self._records = {
                rid: replace(record, active=False)
                for rid, record in self._records.items()
            }
            self._publish_running()
            return
        if event_name == "view-unmapped":
            view_id = _optional_int(view.get("id"))
            if view_id is not None:
                self._records.pop(view_id, None)
            self._publish_running()
            return
        self._matcher.sync_visible_items(self._model.visible_items())
        record = _record_from_view(
            view,
            matcher=self._matcher,
            focused_id=None,
        )
        if record is None:
            if event_name == "view-focused":
                self._records = {
                    rid: replace(existing, active=False)
                    for rid, existing in self._records.items()
                }
                self._publish_running()
            return
        if event_name == "view-focused":
            self._records = {
                rid: _replace_record(
                    existing,
                    active=rid == record.id,
                )
                for rid, existing in self._records.items()
            }
            record = _replace_record(record, active=True)
        elif record.id in self._records:
            existing = self._records[record.id]
            record = _replace_record(
                record,
                active=existing.active,
            )
        self._records[record.id] = record
        self._publish_running()

    def _snapshot_for(self, record: WayfireWindowRecord) -> WindowSnapshot:
        return _snapshot_from_record(
            record, self._outputs, can_minimize=self.supports_minimize
        )

    def _in_scope(self, record: WayfireWindowRecord) -> bool:
        return (
            self._config is None
            or not self._config.current_workspace_only
            or self._snapshot_for(record).on_current_workspace is not False
        )

    def _records_for_desktop(self, desktop_id: str) -> list[WayfireWindowRecord]:
        return [
            record
            for record in self._records.values()
            if record.desktop_id == desktop_id and self._in_scope(record)
        ]

    def _first_record_for_desktop(self, desktop_id: str) -> WayfireWindowRecord | None:
        records = self._records_for_desktop(desktop_id)
        if not records:
            return None
        return next(
            (record for record in records if record.active),
            max(records, key=lambda row: row.last_focus_timestamp),
        )


class WayfireWorkspaceService(WorkspaceService):
    """WorkspaceService backed by Wayfire output/workspace-set snapshots."""

    def __init__(self, *, client: WayfireIpcClient) -> None:
        self._client = client
        self._records: dict[str, WayfireWorkspaceRecord] = {}
        self._watchers: dict[int, Callable[[], None]] = {}
        self._next_watch_id = 1
        self._active_ids: tuple[str, ...] = ()
        self._poll_source_id = 0

    @property
    def supports_switch(self) -> bool:
        return self._client.supports_method("vswitch/set-workspace")

    def start(self) -> None:
        self._refresh()
        if GLib is not None and not self._poll_source_id:
            self._poll_source_id = GLib.timeout_add_seconds(
                WAYFIRE_WINDOW_POLL_SECONDS, self._poll
            )

    def stop(self) -> None:
        if GLib is not None and self._poll_source_id:
            GLib.source_remove(self._poll_source_id)
        self._poll_source_id = 0
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
        record = self._records.get(workspace_id)
        if record is None:
            return ActionResult.NOT_FOUND
        if not self.supports_switch:
            return ActionResult.UNSUPPORTED
        result = self._client.action(
            "vswitch/set-workspace",
            {
                "output-id": record.output_id,
                "x": record.grid_x,
                "y": record.grid_y,
            },
        )
        if result is ActionResult.OK:
            self._refresh()
        return result

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

    def _poll(self) -> bool:
        self._refresh()
        return True


class WayfireDesktopActionService(DesktopActionService):
    """DesktopActionService backed by Wayfire wm-actions/toggle_showdesktop."""

    def __init__(self, *, client: WayfireIpcClient) -> None:
        self._client = client
        self._show_desktop_active: dict[int, bool] = {}

    def start(self) -> None:
        """No persistent runtime resources."""

    def stop(self) -> None:
        self._show_desktop_active.clear()

    def show_desktop(self, show: bool | None = None) -> ActionResult:
        """Toggle or set show-desktop state.

        Wayfire only exposes a toggle method, so for explicit set requests
        the service tracks the last known state to avoid double-toggling.
        """
        try:
            output_id = _focused_output_id(
                self._client.request("window-rules/get-focused-output")
            )
        except (OSError, ValueError, RuntimeError):
            return ActionResult.FAILED
        if output_id is None:
            return ActionResult.NOT_FOUND
        active = self._show_desktop_active.get(output_id, False)
        wants_toggle = show is None or show != active
        if not wants_toggle:
            return ActionResult.OK
        result = self._client.action(
            "wm-actions/toggle_showdesktop",
            {"output-id": output_id},
        )
        if result is ActionResult.OK:
            self._show_desktop_active[output_id] = not active
        return result


# ---------------------------------------------------------------------------
# Visibility / overlap detection
# ---------------------------------------------------------------------------

_VISIBILITY_POLL_MS = 500
_VISIBILITY_DEBOUNCE_MS = 200


class WayfireVisibilityMonitor(VisibilityMonitor):
    """Polls Wayfire view geometry and reports dock-window overlap."""

    def __init__(
        self,
        *,
        client: WayfireIpcClient,
        config: object,
        get_dock_rect: Callable[[], Rect | None],
        on_change: Callable[[bool], None],
    ) -> None:
        self._client = client
        self._config = config
        self._get_dock_rect = get_dock_rect
        self._on_change = on_change
        self._should_hide = False
        self._poll_source_id = 0
        self._debounce_source_id = 0

    def start(self) -> None:
        if GLib is not None and self._poll_source_id == 0:
            self._poll_source_id = GLib.timeout_add(
                _VISIBILITY_POLL_MS,
                self._poll,
            )

    def stop(self) -> None:
        if GLib is not None:
            if self._poll_source_id:
                GLib.source_remove(self._poll_source_id)
            if self._debounce_source_id:
                GLib.source_remove(self._debounce_source_id)
        self._poll_source_id = 0
        self._debounce_source_id = 0

    def evaluate_now(self) -> None:
        if GLib is not None and self._debounce_source_id:
            GLib.source_remove(self._debounce_source_id)
            self._debounce_source_id = 0
        self._do_evaluate()

    def _poll(self) -> bool:
        if GLib is not None and self._debounce_source_id:
            GLib.source_remove(self._debounce_source_id)
        if GLib is not None:
            self._debounce_source_id = GLib.timeout_add(
                _VISIBILITY_DEBOUNCE_MS,
                self._do_evaluate,
            )
        return True

    def _do_evaluate(self) -> bool:
        self._debounce_source_id = 0
        should_hide = self._evaluate()
        if should_hide != self._should_hide:
            self._should_hide = should_hide
            self._on_change(should_hide)
        return False

    def _evaluate(self) -> bool:
        mode = getattr(self._config, "hide_mode_enum", None)
        if mode is None:
            return False
        # HideMode enum values: NONE=0, AUTOHIDE=1, INTELLIGENT, DODGE_ACTIVE,
        # WINDOW_DODGE, DODGE_MAXIMIZED
        mode_value = getattr(mode, "value", mode)
        if isinstance(mode_value, str):
            if mode_value in ("none", "autohide"):
                return False
        elif isinstance(mode_value, int) and mode_value < 2:
            return False

        dock_rect = self._get_dock_rect()
        if dock_rect is None:
            return False

        try:
            views = self._client.request("window-rules/list-views")
            focused = self._client.request("window-rules/get-focused-view")
        except (OSError, json.JSONDecodeError, RuntimeError):
            return False

        focused_id = _focused_view_id(focused)
        outputs = _load_outputs(self._client)
        windows: list[tuple[Rect | None, bool, bool, bool]] = []
        for item in views if isinstance(views, list) else []:
            if not isinstance(item, Mapping):
                continue
            record = _record_from_view(
                item, matcher=_NullMatcher(), focused_id=focused_id
            )
            if record is None:
                continue
            snapshot = _snapshot_from_record(record, outputs)
            if snapshot.visible is False or snapshot.on_current_workspace is False:
                continue
            geometry = snapshot.geometry
            output = outputs.get(record.output_id)
            output_rect = _rect_from_mapping(output.get("geometry")) if output else None
            if geometry is not None and output_rect is not None:
                geometry = Rect(
                    max(geometry.x, output_rect.x),
                    max(geometry.y, output_rect.y),
                    max(
                        0,
                        min(geometry.right, output_rect.right)
                        - max(geometry.x, output_rect.x),
                    ),
                    max(
                        0,
                        min(geometry.bottom, output_rect.bottom)
                        - max(geometry.y, output_rect.y),
                    ),
                )
            windows.append(
                (
                    geometry,
                    snapshot.active,
                    bool(snapshot.minimized),
                    bool(snapshot.fullscreen or snapshot.maximized),
                )
            )

        if isinstance(mode_value, str):
            if mode_value == "intelligent":
                return self._eval_dodge_active(dock_rect, windows)
            if mode_value == "dodge-active":
                return self._eval_dodge_active(dock_rect, windows)
            if mode_value == "window-dodge":
                return self._eval_window_dodge(dock_rect, windows)
            if mode_value == "dodge-maximized":
                return self._eval_dodge_maximized(dock_rect, windows)
        return False

    @staticmethod
    def _eval_dodge_active(
        dock_rect: Rect,
        windows: list[tuple[Rect | None, bool, bool, bool]],
    ) -> bool:
        for geometry, is_active, is_minimized, _is_fullscreen in windows:
            if is_minimized:
                continue
            if not is_active:
                continue
            if geometry is not None and dock_rect.overlaps(geometry):
                return True
        return False

    @staticmethod
    def _eval_window_dodge(
        dock_rect: Rect,
        windows: list[tuple[Rect | None, bool, bool, bool]],
    ) -> bool:
        for geometry, _is_active, is_minimized, _is_fullscreen in windows:
            if is_minimized:
                continue
            if geometry is not None and dock_rect.overlaps(geometry):
                return True
        return False

    @staticmethod
    def _eval_dodge_maximized(
        dock_rect: Rect,
        windows: list[tuple[Rect | None, bool, bool, bool]],
    ) -> bool:
        for geometry, _is_active, is_minimized, is_maximized in windows:
            if (
                not is_minimized
                and is_maximized
                and geometry is not None
                and dock_rect.overlaps(geometry)
            ):
                return True
        return False


class WayfireVisibilityService(VisibilityService):
    """VisibilityService that creates Wayfire overlap monitors."""

    def __init__(self, *, client: WayfireIpcClient, config: object) -> None:
        self._client = client
        self._config = config

    def start(self) -> None:
        """No service-level runtime loop is needed."""

    def stop(self) -> None:
        """No service-level resources are held."""

    def create_monitor(
        self,
        *,
        get_dock_rect: Callable[[], Rect | None],
        on_change: Callable[[bool], None],
    ) -> VisibilityMonitor | None:
        return WayfireVisibilityMonitor(
            client=self._client,
            config=self._config,
            get_dock_rect=get_dock_rect,
            on_change=on_change,
        )


class WayfireWindowPickService(WindowPickService):
    """WindowPickService backed by Wayfire view geometry and PID metadata."""

    def __init__(self, *, client: WayfireIpcClient) -> None:
        self._client = client
        self._records: dict[int, WayfireWindowRecord] = {}
        self._outputs: dict[int, Mapping[str, JsonValue]] = {}

    def start(self) -> None:
        self._refresh()

    def stop(self) -> None:
        self._records.clear()
        self._outputs.clear()

    def pick_window_at(self, *, x: int, y: int) -> WindowSnapshot | None:
        self._refresh()
        candidates = []
        for record in self._records.values():
            snapshot = self._snapshot_for(record)
            geometry = snapshot.geometry
            output = self._outputs.get(record.output_id)
            output_rect = _rect_from_mapping(output.get("geometry")) if output else None
            if output_rect is not None and not (
                output_rect.x <= x < output_rect.right
                and output_rect.y <= y < output_rect.bottom
            ):
                continue
            if (
                geometry is not None
                and geometry.x <= x < geometry.right
                and geometry.y <= y < geometry.bottom
                and snapshot.visible is not False
                and snapshot.on_current_workspace is not False
                and not snapshot.minimized
            ):
                candidates.append((record, snapshot))
        if not candidates:
            return None
        # list-views is allocation order, not stacking order. Focus metadata
        # supplies a better target when overlapping windows have been raised.
        return max(
            candidates,
            key=lambda pair: (pair[0].active, pair[0].last_focus_timestamp, pair[0].id),
        )[1]

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
        self._outputs = _load_outputs(self._client)
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

    def _snapshot_for(self, record: WayfireWindowRecord) -> WindowSnapshot:
        return _snapshot_from_record(record, self._outputs)


def load_wayfire_window_service(
    *,
    model,
    application_registry: ApplicationRegistry,
    process_identity_service: ProcessIdentityService,
    config: Config | None = None,
) -> WayfireWindowService | None:
    """Return a Wayfire WindowService when the IPC socket is detectable."""
    socket_path = wayfire_socket_path()
    if socket_path is None or not socket_path.exists():
        return None
    client = WayfireIpcClient(socket_path=socket_path)
    if not _has_snapshot(client, "window-rules/list-views"):
        return None
    # Create the service first without a watcher, then wire one up that
    # calls back into the service's _on_event handler.
    service = WayfireWindowService(
        model=model,
        application_registry=application_registry,
        process_identity_service=process_identity_service,
        client=client,
        config=config,
    )
    watcher = WayfireEventWatcher(
        socket_path=str(socket_path),
        on_event=service._on_event,
    )
    service._watcher = watcher
    return service


def load_wayfire_workspace_service() -> WayfireWorkspaceService | None:
    """Return a Wayfire WorkspaceService when the IPC socket is detectable."""
    socket_path = wayfire_socket_path()
    if socket_path is None or not socket_path.exists():
        return None
    client = WayfireIpcClient(socket_path=socket_path)
    if not _has_snapshot(client, "window-rules/list-outputs"):
        return None
    return WayfireWorkspaceService(client=client)


def load_wayfire_window_pick_service() -> WayfireWindowPickService | None:
    """Return a Wayfire WindowPickService when the IPC socket is detectable."""
    socket_path = wayfire_socket_path()
    if socket_path is None or not socket_path.exists():
        return None
    client = WayfireIpcClient(socket_path=socket_path)
    if not _has_snapshot(client, "window-rules/list-views"):
        return None
    return WayfireWindowPickService(client=client)


def load_wayfire_desktop_action_service() -> WayfireDesktopActionService | None:
    """Return a Wayfire DesktopActionService when the IPC socket is detectable."""
    socket_path = wayfire_socket_path()
    if socket_path is None or not socket_path.exists():
        return None
    client = WayfireIpcClient(socket_path=socket_path)
    if not client.supports_method("wm-actions/toggle_showdesktop"):
        return None
    return WayfireDesktopActionService(client=client)


def load_wayfire_visibility_service(
    *, config: object
) -> WayfireVisibilityService | None:
    """Return a Wayfire VisibilityService when the IPC socket is detectable."""
    socket_path = wayfire_socket_path()
    if socket_path is None or not socket_path.exists():
        return None
    client = WayfireIpcClient(socket_path=socket_path)
    if not _has_snapshot(client, "window-rules/list-views"):
        return None
    return WayfireVisibilityService(client=client, config=config)


def load_wayfire_preview_service() -> None:
    """Preview is not supported on Wayfire."""


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


def _read_message(sock: socket.socket) -> bytes:
    size = struct.unpack("I", _read_exact(sock, 4))[0]
    if size > _MAX_IPC_MESSAGE_BYTES:
        raise RuntimeError("Wayfire IPC frame exceeds size limit")
    return _read_exact(sock, size)


def _has_snapshot(client: WayfireIpcClient, method: str) -> bool:
    try:
        return isinstance(client.request(method), list)
    except (OSError, ValueError, RuntimeError):
        return False


def _load_outputs(client: WayfireIpcClient) -> dict[int, Mapping[str, JsonValue]]:
    try:
        outputs = client.request("window-rules/list-outputs")
    except (OSError, ValueError, RuntimeError):
        return {}
    result = {}
    for output in outputs if isinstance(outputs, list) else []:
        # as_json_object also proves the keys are strings, which the declared
        # return type requires; a bare Mapping check leaves them Unknown.
        output_object = as_json_object(output)
        if output_object is not None:
            output_id = _optional_int(output_object.get("id"))
            if output_id is not None:
                result[output_id] = output_object
    return result


def _snapshot_from_record(
    record: WayfireWindowRecord,
    outputs: Mapping[int, Mapping[str, JsonValue]],
    *,
    can_minimize: bool = False,
) -> WindowSnapshot:
    """Project current-workspace-relative rectangles into global screen space."""
    output = outputs.get(record.output_id) if record.output_id is not None else None
    output_rect = _rect_from_mapping(output.get("geometry")) if output else None
    geometry = record.geometry
    workspace_id = None
    on_current = None
    visible = False if record.minimized else None
    if record.output_id is not None and record.output_id < 0:
        on_current = visible = False
    if output is not None:
        output_wset = _optional_int(output.get("wset-index"))
        if (
            record.wset_index is not None
            and output_wset is not None
            and record.wset_index != output_wset
        ):
            on_current = visible = False
        elif (
            output_rect
            and output_rect.width > 0
            and output_rect.height > 0
            and geometry
        ):
            on_current = (
                geometry.width > 0
                and geometry.height > 0
                and geometry.overlaps(Rect(0, 0, output_rect.width, output_rect.height))
            )
            visible = on_current and not record.minimized
            workspace = as_json_object(output.get("workspace"))
            if workspace is not None:
                active_x = _optional_int(workspace.get("x"))
                active_y = _optional_int(workspace.get("y"))
                width = _optional_int(workspace.get("grid_width"))
                height = _optional_int(workspace.get("grid_height"))
                if active_x is not None and active_y is not None and width and height:
                    # Same center-based, clamped main workspace used by Wayfire.
                    x = active_x + math.floor(
                        (geometry.x + geometry.width / 2) / output_rect.width
                    )
                    y = active_y + math.floor(
                        (geometry.y + geometry.height / 2) / output_rect.height
                    )
                    x = min(max(x, 0), width - 1)
                    y = min(max(y, 0), height - 1)
                    workspace_id = f"{record.output_id}:{x},{y}"
        if output_rect is not None and geometry is not None:
            geometry = Rect(
                geometry.x + output_rect.x,
                geometry.y + output_rect.y,
                geometry.width,
                geometry.height,
            )
    return WindowSnapshot(
        id=record.window_id,
        desktop_id=record.desktop_id or "",
        title=record.title,
        app_id=record.app_id or None,
        active=record.active,
        minimized=record.minimized,
        fullscreen=record.fullscreen,
        maximized=record.maximized,
        geometry=geometry,
        workspace_id=workspace_id,
        on_current_workspace=on_current,
        sticky=record.sticky,
        can_activate=True,
        can_minimize=can_minimize,
        can_close=True,
        pid=record.pid,
        visible=visible,
    )


def _record_from_view(
    view: Mapping[str, JsonValue],
    *,
    matcher: AppIdMatcher | _NullMatcher,
    focused_id: int | None,
) -> WayfireWindowRecord | None:
    if not bool(view.get("mapped", True)):
        return None
    view_type = str(view.get("type", "") or "").strip().lower()
    role = str(view.get("role", "") or "").strip().lower()
    layer = str(view.get("layer", "") or "").strip().lower()
    if view_type != "toplevel" and role != "toplevel" and layer != "workspace":
        return None
    view_id = _optional_int(view.get("id"))
    if view_id is None:
        return None
    app_id = str(view.get("app-id", "") or "").strip()
    pid = _optional_int(view.get("pid"))
    match = matcher.match_result(app_id, process_id=pid) if app_id else None
    wset_index = _optional_int(view.get("wset-index"))
    tiled_edges = _optional_int(view.get("tiled-edges"))
    return WayfireWindowRecord(
        id=view_id,
        title=str(view.get("title", "") or "Window"),
        app_id=app_id,
        application_match=match,
        # Activation may persist on another output/workspace. Keyboard focus
        # from get-focused-view is the authoritative active-window identity.
        active=view_id == focused_id,
        minimized=_optional_bool(view.get("minimized")),
        fullscreen=_optional_bool(view.get("fullscreen")),
        geometry=_rect_from_mapping(view.get("geometry")),
        workspace_id=None,
        pid=pid,
        output_id=_optional_int(view.get("output-id")),
        wset_index=wset_index if wset_index is not None and wset_index >= 0 else None,
        maximized=tiled_edges == 15 if tiled_edges is not None else None,
        sticky=bool(view.get("sticky", False)),
        last_focus_timestamp=_optional_int(view.get("last-focus-timestamp")) or 0,
    )


def _rect_from_mapping(value: object) -> Rect | None:
    if not isinstance(value, Mapping):
        return None
    x = _optional_int(value.get("x", 0))
    y = _optional_int(value.get("y", 0))
    width = _optional_int(value.get("width", 0))
    height = _optional_int(value.get("height", 0))
    if x is None or y is None or width is None or height is None:
        return None
    return Rect(x=x, y=y, width=width, height=height)


def _workspace_records_from_output(
    output: Mapping[str, JsonValue],
    *,
    number_offset: int,
    focused_output_id: int | None,
) -> list[WayfireWorkspaceRecord]:
    output_id = _optional_int(output.get("id"))
    workspace = as_json_object(output.get("workspace"))
    if workspace is None:
        return []
    grid_width = _optional_int(workspace.get("grid_width", 1))
    grid_height = _optional_int(workspace.get("grid_height", 1))
    active_x = _optional_int(workspace.get("x", 0))
    active_y = _optional_int(workspace.get("y", 0))
    if (
        grid_width is None
        or grid_height is None
        or active_x is None
        or active_y is None
    ):
        return []
    row_output_id = output_id if output_id is not None else 0
    output_name = str(output.get("name", "") or output_id or "output")
    records: list[WayfireWorkspaceRecord] = []
    for y in range(max(1, grid_height)):
        for x in range(max(1, grid_width)):
            number = number_offset + len(records) + 1
            identity = output_id if output_id is not None else output_name
            record_id = f"{identity}:{x},{y}"
            active = output_id == focused_output_id and x == active_x and y == active_y
            records.append(
                WayfireWorkspaceRecord(
                    id=record_id,
                    number=number,
                    name=f"{output_name} {x + 1},{y + 1}",
                    active=active,
                    output_id=row_output_id,
                    grid_x=x,
                    grid_y=y,
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
    def match_result(
        _app_id: str,
        *,
        process_id: int | None = None,
    ) -> ApplicationMatch | None:
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
    if not isinstance(value, int | float | str):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _replace_record(
    record: WayfireWindowRecord,
    *,
    active: bool | None = None,
) -> WayfireWindowRecord:
    """Return a copy of *record* with the given fields replaced."""
    if active is None:
        return record
    return replace(record, active=active)
