# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Hyprland IPC helpers and WindowService.

Hyprland exposes a synchronous command socket and a separate event stream.
Command calls in this module are intentionally short-lived: connect, send one
request, read the reply, close. Holding the command socket open can stall the
compositor.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docking.log import get_logger
from docking.platform.app_matcher import AppIdMatcher
from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    Rect,
    WindowId,
    WindowService,
    WindowSnapshot,
)
from docking.platform.running import RunningAppInfo, RunningWindowInfo

log = get_logger(name="hyprland_ipc")

_TASKBAR_EVENTS = {
    "openwindow",
    "closewindow",
    "activewindowv2",
    "windowtitlev2",
    "movewindowv2",
    "workspacev2",
    "focusedmonv2",
    "urgent",
    "fullscreen",
    "pin",
    "minimized",
}


@dataclass(frozen=True)
class HyprlandSocketPaths:
    """Hyprland command and event socket paths."""

    command: Path
    events: Path


@dataclass(frozen=True)
class HyprlandEvent:
    """One parsed line from Hyprland's event stream socket."""

    name: str
    data: str

    @property
    def fields(self) -> tuple[str, ...]:
        """Return comma-separated event payload fields."""
        if not self.data:
            return ()
        return tuple(part.strip() for part in self.data.split(","))


@dataclass(frozen=True)
class HyprlandWindowRecord:
    """Backend-neutral projection of one Hyprland client JSON object."""

    address: str
    title: str
    app_id: str
    desktop_id: str | None
    active: bool
    urgent: bool
    minimized: bool | None
    fullscreen: bool | None
    pinned: bool | None
    geometry: Rect | None
    workspace_id: str | None

    @property
    def window_id(self) -> WindowId:
        return WindowId(backend=DisplayServer.WAYLAND, value=self.address)


class HyprlandIpcClient:
    """Short-lived request client for Hyprland's command socket."""

    def __init__(
        self,
        *,
        paths: HyprlandSocketPaths,
        socket_factory: Callable[[int, int], socket.socket] = socket.socket,
        timeout: float = 1.0,
    ) -> None:
        self._paths = paths
        self._socket_factory = socket_factory
        self._timeout = timeout

    def request(self, command: str) -> str:
        """Send one raw Hyprland IPC command and return the raw response."""
        with self._socket_factory(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self._timeout)
            sock.connect(str(self._paths.command))
            sock.sendall(command.encode("utf-8"))
            sock.shutdown(socket.SHUT_WR)
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")

    def query_json(self, command: str) -> Any:
        """Run a `hyprctl -j` equivalent query."""
        response = self.request(f"j/{command}")
        return json.loads(response or "null")

    def dispatch(self, command: str) -> ActionResult:
        """Run one dispatcher command."""
        try:
            response = self.request(f"dispatch {command}").strip().lower()
        except OSError:
            return ActionResult.FAILED
        if response in {"ok", "ok\n"}:
            return ActionResult.OK
        return ActionResult.FAILED


class HyprlandEventStream:
    """Background reader for Hyprland's event socket."""

    def __init__(
        self,
        *,
        paths: HyprlandSocketPaths,
        callback: Callable[[HyprlandEvent], None],
        socket_factory: Callable[[int, int], socket.socket] = socket.socket,
        timeout: float = 1.0,
    ) -> None:
        self._paths = paths
        self._callback = callback
        self._socket_factory = socket_factory
        self._timeout = timeout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None

    def start(self) -> None:
        """Start the reader thread once."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="docking-hyprland-events",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the reader and close its socket."""
        self._stop.set()
        sock = self._socket
        if sock is not None:
            with suppress(OSError):
                sock.close()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self._timeout)
        self._thread = None
        self._socket = None

    def _run(self) -> None:
        try:
            with self._socket_factory(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                self._socket = sock
                sock.settimeout(self._timeout)
                sock.connect(str(self._paths.events))
                sock.settimeout(None)
                file_obj = sock.makefile("rb")
                for raw_line in file_obj:
                    if self._stop.is_set():
                        break
                    event = parse_hyprland_event(raw_line.decode("utf-8", "replace"))
                    if event is not None:
                        self._callback(event)
        except OSError as exc:
            if not self._stop.is_set():
                log.info("Hyprland event stream stopped: %s", exc)


class HyprlandWindowService(WindowService):
    """WindowService backed by Hyprland IPC snapshots and events."""

    def __init__(
        self,
        *,
        model,
        launcher,
        client: HyprlandIpcClient,
        event_stream_factory: Callable[
            [Callable[[HyprlandEvent], None]], HyprlandEventStream | None
        ],
        preview_handle_source: object | None = None,
    ) -> None:
        self._model = model
        self._matcher = AppIdMatcher(launcher=launcher)
        self._client = client
        self._event_stream_factory = event_stream_factory
        self._event_stream: HyprlandEventStream | None = None
        self._preview_handle_source = preview_handle_source
        self._records_by_address: dict[str, HyprlandWindowRecord] = {}

    def set_preview_handle_source(self, source: object | None) -> None:
        """Set a companion protocol service used only for preview handle lookup."""
        self._preview_handle_source = source

    def start(self) -> None:
        """Load initial clients and subscribe to taskbar-relevant changes."""
        self._start_preview_handle_source()
        self._refresh()
        self._event_stream = self._event_stream_factory(self._on_event)
        if self._event_stream is not None:
            self._event_stream.start()

    def stop(self) -> None:
        """Stop event delivery and clear published running state."""
        if self._event_stream is not None:
            self._event_stream.stop()
            self._event_stream = None
        self._stop_preview_handle_source()
        self._records_by_address.clear()
        self._model.update_running(running={})

    def list_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        """Return known Hyprland windows for one desktop ID."""
        return tuple(
            self._snapshot_for(record)
            for record in self._records_by_address.values()
            if record.desktop_id == desktop_id
        )

    def list_preview_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        """Return menu rows; preview capture remains a separate protocol path."""
        return self.list_windows(desktop_id=desktop_id)

    def icon_name_for_desktop(self, desktop_id: str) -> str:
        """Return a generic icon fallback; launcher/model handle app icons."""
        return "application-x-executable"

    def protocol_handle_for_window_id(self, window_id: WindowId) -> object | None:
        """Return a companion foreign-toplevel handle for Hyprland preview capture."""
        address = _window_address(window_id)
        if address is None:
            return None
        record = self._records_by_address.get(address)
        if record is None:
            return None
        return self._preview_handle_for_record(record)

    def activate(self, window_id: WindowId) -> ActionResult:
        """Focus a Hyprland window by address."""
        address = _window_address(window_id)
        if address is None:
            return ActionResult.NOT_FOUND
        if address not in self._records_by_address:
            return ActionResult.NOT_FOUND
        return self._client.dispatch(f"focuswindow address:{address}")

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
        """Minimize all known windows for an app."""
        records = self._records_for_desktop(desktop_id)
        if not records:
            return ActionResult.NOT_FOUND
        result = ActionResult.OK
        for record in records:
            action = self._client.dispatch(
                f"setprop address:{record.address} minimized 1"
            )
            if action is not ActionResult.OK:
                result = action
        return result

    def close(self, window_id: WindowId) -> ActionResult:
        """Close a Hyprland window by address."""
        address = _window_address(window_id)
        if address is None:
            return ActionResult.NOT_FOUND
        if address not in self._records_by_address:
            return ActionResult.NOT_FOUND
        return self._client.dispatch(f"closewindow address:{address}")

    def close_all(self, desktop_id: str) -> ActionResult:
        """Close all known windows for an app."""
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
        """Close the active window for an app, falling back to the first one."""
        record = self._first_record_for_desktop(desktop_id)
        if record is None:
            return ActionResult.NOT_FOUND
        return self.close(record.window_id)

    def toggle_focus(self, desktop_id: str) -> ActionResult:
        """Focus inactive apps; minimize active apps when possible."""
        record = self._first_record_for_desktop(desktop_id)
        if record is None:
            return ActionResult.NOT_FOUND
        if record.active:
            return self._client.dispatch(
                f"setprop address:{record.address} minimized 1"
            )
        return self.activate(record.window_id)

    def _on_event(self, event: HyprlandEvent) -> None:
        if event.name in _TASKBAR_EVENTS:
            self._refresh()

    def _refresh(self) -> None:
        try:
            clients = self._client.query_json("clients")
            active = self._client.query_json("activewindow")
        except (OSError, json.JSONDecodeError) as exc:
            log.info("Hyprland IPC snapshot unavailable: %s", exc)
            return
        active_address = (
            _normalize_address(_mapping_value(active, "address", ""))
            if isinstance(active, Mapping)
            else ""
        )
        if not isinstance(clients, list):
            clients = []
        self._matcher.sync_visible_items(self._model.visible_items())
        records: dict[str, HyprlandWindowRecord] = {}
        for item in clients:
            if not isinstance(item, Mapping):
                continue
            record = _record_from_client(
                item,
                matcher=self._matcher,
                active_address=active_address,
            )
            if record is not None:
                records[record.address] = record
        self._records_by_address = records
        self._publish_running()

    def _publish_running(self) -> None:
        windows_by_desktop: dict[str, list[RunningWindowInfo]] = {}
        for record in self._records_by_address.values():
            if record.desktop_id is None:
                continue
            windows_by_desktop.setdefault(record.desktop_id, []).append(
                RunningWindowInfo(
                    desktop_id=record.desktop_id,
                    xid=0,
                    window_id=record.window_id,
                    active=record.active,
                    urgent=record.urgent,
                    window=record.address,
                )
            )
        self._model.update_running(
            running={
                desktop_id: RunningAppInfo.from_windows(items)
                for desktop_id, items in windows_by_desktop.items()
            }
        )

    def _snapshot_for(self, record: HyprlandWindowRecord) -> WindowSnapshot:
        return WindowSnapshot(
            id=record.window_id,
            desktop_id=record.desktop_id or "",
            title=record.title,
            app_id=record.app_id or None,
            active=record.active,
            urgent=record.urgent,
            minimized=record.minimized,
            fullscreen=record.fullscreen,
            geometry=record.geometry,
            workspace_id=record.workspace_id,
            can_activate=True,
            can_minimize=True,
            can_close=True,
            can_preview=self._preview_handle_for_record(record) is not None,
        )

    def _records_for_desktop(self, desktop_id: str) -> list[HyprlandWindowRecord]:
        return [
            record
            for record in self._records_by_address.values()
            if record.desktop_id == desktop_id
        ]

    def _first_record_for_desktop(self, desktop_id: str) -> HyprlandWindowRecord | None:
        records = self._records_for_desktop(desktop_id)
        if not records:
            return None
        return next((record for record in records if record.active), records[0])

    def _preview_handle_for_record(self, record: HyprlandWindowRecord) -> object | None:
        source = self._preview_handle_source
        handle_for_match = getattr(source, "protocol_handle_for_match", None)
        if not callable(handle_for_match):
            return None
        return handle_for_match(
            desktop_id=record.desktop_id,
            app_id=record.app_id,
            title=record.title,
        )

    def _start_preview_handle_source(self) -> None:
        start = getattr(self._preview_handle_source, "start", None)
        if callable(start):
            start()

    def _stop_preview_handle_source(self) -> None:
        stop = getattr(self._preview_handle_source, "stop", None)
        if callable(stop):
            stop()


def load_hyprland_window_service(*, model, launcher) -> HyprlandWindowService | None:
    """Return a Hyprland WindowService when IPC sockets are detectable."""
    paths = hyprland_socket_paths()
    if paths is None or not paths.command.exists() or not paths.events.exists():
        return None
    client = HyprlandIpcClient(paths=paths)
    return HyprlandWindowService(
        model=model,
        launcher=launcher,
        client=client,
        event_stream_factory=lambda callback: HyprlandEventStream(
            paths=paths,
            callback=callback,
        ),
    )


def hyprland_socket_paths(
    environ: Mapping[str, str] | None = None,
) -> HyprlandSocketPaths | None:
    """Return expected Hyprland socket paths from the process environment."""
    env = os.environ if environ is None else environ
    signature = env.get("HYPRLAND_INSTANCE_SIGNATURE", "").strip()
    runtime_dir = env.get("XDG_RUNTIME_DIR", "").strip()
    if not signature or not runtime_dir:
        return None
    root = Path(runtime_dir) / "hypr" / signature
    return HyprlandSocketPaths(
        command=root / ".socket.sock",
        events=root / ".socket2.sock",
    )


def parse_hyprland_event(line: str) -> HyprlandEvent | None:
    """Parse one `EVENT>>DATA` Hyprland event stream line."""
    stripped = line.strip()
    if not stripped or ">>" not in stripped:
        return None
    name, data = stripped.split(">>", 1)
    name = name.strip()
    if not name:
        return None
    return HyprlandEvent(name=name, data=data.strip())


def _record_from_client(
    item: Mapping[str, Any],
    *,
    matcher: AppIdMatcher,
    active_address: str,
) -> HyprlandWindowRecord | None:
    address = _normalize_address(_mapping_value(item, "address", ""))
    if not address:
        return None
    app_id = str(
        _mapping_value(item, "class", "")
        or _mapping_value(item, "initialClass", "")
        or ""
    ).strip()
    desktop_id = matcher.match(app_id) if app_id else None
    workspace = _mapping_value(item, "workspace", {})
    workspace_id = None
    if isinstance(workspace, Mapping):
        raw_workspace = _mapping_value(workspace, "id", "")
        workspace_id = str(raw_workspace) if raw_workspace != "" else None
    geometry = _geometry_from_client(item)
    return HyprlandWindowRecord(
        address=address,
        title=str(_mapping_value(item, "title", "") or "Window"),
        app_id=app_id,
        desktop_id=desktop_id,
        active=address == active_address,
        urgent=bool(_mapping_value(item, "urgent", False)),
        minimized=_optional_bool(_mapping_value(item, "minimized", None)),
        fullscreen=_optional_bool(_mapping_value(item, "fullscreen", None)),
        pinned=_optional_bool(_mapping_value(item, "pinned", None)),
        geometry=geometry,
        workspace_id=workspace_id,
    )


def _geometry_from_client(item: Mapping[str, Any]) -> Rect | None:
    at = _mapping_value(item, "at", None)
    size = _mapping_value(item, "size", None)
    if (
        not isinstance(at, Sequence)
        or isinstance(at, str | bytes)
        or len(at) < 2
        or not isinstance(size, Sequence)
        or isinstance(size, str | bytes)
        or len(size) < 2
    ):
        return None
    try:
        return Rect(
            x=int(at[0]),
            y=int(at[1]),
            width=int(size[0]),
            height=int(size[1]),
        )
    except (TypeError, ValueError):
        return None


def _mapping_value(mapping: Mapping[str, Any], key: str, default: Any) -> Any:
    return mapping.get(key, default)


def _normalize_address(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text.startswith("0x"):
        return text
    return f"0x{text}"


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    return None


def _window_address(window_id: WindowId) -> str | None:
    if window_id.backend is not DisplayServer.WAYLAND:
        return None
    address = _normalize_address(window_id.value)
    return address or None
