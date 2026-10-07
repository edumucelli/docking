"""Sway's public i3 IPC: bounded queries and main-loop event delivery."""

from __future__ import annotations

import json
import socket
import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from gi.repository import GLib

from docking.log import get_logger
from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    Rect,
    WindowId,
    WindowSnapshot,
    WorkspaceService,
    WorkspaceSnapshot,
)
from docking.platform.backends.native_windows import NativeWindowService
from docking.platform.backends.wayland.previews import WaylandPreviewHandleTracker

log = get_logger(name="sway_ipc")
_HEADER = struct.Struct("<6sII")
_MAX_REPLY = 16 * 1024 * 1024


def _receive(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise OSError("Sway IPC disconnected")
        chunks.extend(chunk)
    return bytes(chunks)


class SwayIpcClient:
    def __init__(self, path: str) -> None:
        self.path = path

    def query(self, kind: int, payload: str = "") -> object:
        encoded = payload.encode("utf-8")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            sock.connect(self.path)
            sock.sendall(_HEADER.pack(b"i3-ipc", len(encoded), kind) + encoded)
            magic, length, reply_kind = _HEADER.unpack(_receive(sock, _HEADER.size))
            if magic != b"i3-ipc" or length > _MAX_REPLY or reply_kind != kind:
                raise OSError("Invalid Sway IPC reply")
            return json.loads(_receive(sock, length))

    def command(self, command: str) -> ActionResult:
        try:
            reply = self.query(0, command)
            return (
                ActionResult.OK
                if isinstance(reply, list)
                and reply
                and all(
                    isinstance(row, dict) and row.get("success") is True
                    for row in reply
                )
                else ActionResult.FAILED
            )
        except (OSError, ValueError):
            return ActionResult.FAILED


def windows_from_tree(
    tree: Mapping, visible_workspaces: set[str]
) -> tuple[WindowSnapshot, ...]:
    """Global frame rectangles; inactive workspaces and stacked siblings stay hidden."""
    windows: list[WindowSnapshot] = []

    def visit(node: Mapping, workspace: str | None, visible: bool) -> None:
        if node.get("type") == "workspace":
            workspace = str(node.get("id"))
            visible = workspace in visible_workspaces
        children = [row for row in node.get("nodes", []) if isinstance(row, Mapping)]
        floating = [
            row for row in node.get("floating_nodes", []) if isinstance(row, Mapping)
        ]
        raw_id = node.get("id")
        if isinstance(raw_id, int) and (node.get("app_id") or node.get("window")):
            raw = node.get("rect", {})
            rect = None
            if isinstance(raw, Mapping) and all(
                isinstance(raw.get(key), int) for key in ("x", "y", "width", "height")
            ):
                rect = Rect(raw["x"], raw["y"], raw["width"], raw["height"])
            props = node.get("window_properties") or {}
            windows.append(
                WindowSnapshot(
                    id=WindowId(DisplayServer.WAYLAND, f"sway:{raw_id}"),
                    desktop_id="",
                    title=str(node.get("name") or "Window"),
                    app_id=node.get("app_id")
                    or props.get("class")
                    or props.get("instance"),
                    active=node.get("focused") is True,
                    urgent=node.get("urgent") is True,
                    minimized=False,
                    fullscreen=bool(node.get("fullscreen_mode")),
                    geometry=rect,
                    workspace_id=workspace,
                    on_current_workspace=workspace in visible_workspaces,
                    can_activate=True,
                    can_close=True,
                    visible=visible,
                    pid=node.get("pid"),
                )
            )
        focused_child = next(iter(node.get("focus", [])), None)
        stacked = node.get("layout") in {"tabbed", "stacked"}
        for child in children:
            visit(
                child,
                workspace,
                visible and (not stacked or child.get("id") == focused_child),
            )
        for child in floating:
            visit(child, workspace, visible)

    visit(tree, None, False)
    return tuple(windows)


class SwayWindowService(NativeWindowService):
    def __init__(
        self,
        *,
        client: SwayIpcClient,
        preview_handles: WaylandPreviewHandleTracker | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._socket: socket.socket | None = None
        self._source = 0
        self._pending = 0
        self._buffer = bytearray()
        self._preview_handles = preview_handles

    def start(self) -> None:
        if self._socket is not None:
            return
        self.refresh()
        sock = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(0.25)
            sock.connect(self._client.path)
            payload = b'["window","workspace","output"]'
            sock.sendall(_HEADER.pack(b"i3-ipc", len(payload), 2) + payload)
            sock.setblocking(False)
            self._socket = sock
            self._source = GLib.io_add_watch(
                sock.fileno(), GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._read
            )
        except OSError as exc:
            log.info("Sway subscription unavailable: %s", exc)
            if sock is not None:
                sock.close()

    def stop(self) -> None:
        for source in (self._source, self._pending):
            if source:
                GLib.source_remove(source)
        self._source = self._pending = 0
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self._buffer.clear()
        super().stop()

    def refresh(self) -> None:
        try:
            workspaces = self._client.query(1)
            tree = self._client.query(4)
            visible = (
                {
                    str(row.get("id"))
                    for row in workspaces
                    if isinstance(row, Mapping) and row.get("visible") is True
                }
                if isinstance(workspaces, list)
                else set()
            )
            self.replace_windows(
                windows_from_tree(tree, visible) if isinstance(tree, Mapping) else ()
            )
        except (OSError, ValueError) as exc:
            log.debug("Sway snapshot unavailable: %s", exc)
            self.replace_windows(())

    def list_all_windows(self) -> Sequence[WindowSnapshot]:
        rows = super().list_all_windows()
        handles = self._preview_handles
        if handles is None:
            return rows
        result = []
        for row in rows:
            handles.associate_window(
                window_id=row.id,
                desktop_id=row.desktop_id,
                app_id=row.app_id or "",
                title=row.title,
            )
            result.append(replace(row, can_preview=handles.can_preview(row.id)))
        return tuple(result)

    def list_windows(self, desktop_id: str) -> Sequence[WindowSnapshot]:
        return tuple(
            row
            for row in self.list_all_windows()
            if row.desktop_id == desktop_id and self._taskbar_window(row)
        )

    def _read(self, _fd: int, condition: int) -> bool:
        if self._socket is None:
            return False
        try:
            data = self._socket.recv(65536)
            if not data:
                raise OSError("Sway event socket closed")
            self._buffer.extend(data)
            while len(self._buffer) >= _HEADER.size:
                magic, length, kind = _HEADER.unpack_from(self._buffer)
                if magic != b"i3-ipc" or length > _MAX_REPLY:
                    raise OSError("Invalid Sway event frame")
                if len(self._buffer) < _HEADER.size + length:
                    break
                del self._buffer[: _HEADER.size + length]
                if kind & (1 << 31) and not self._pending:
                    self._pending = GLib.idle_add(self._dispatch)
        except BlockingIOError:
            return True
        except OSError:
            self._source = 0
            self._socket.close()
            self._socket = None
            self.replace_windows(())
            return False
        return True

    def _dispatch(self) -> bool:
        self._pending = 0
        self.refresh()
        return False

    def perform(self, window_id: WindowId, action: str) -> ActionResult:
        raw_id = str(window_id.value).removeprefix("sway:")
        if not raw_id.isdecimal():
            return ActionResult.NOT_FOUND
        command = {"activate": "focus", "close": "kill"}.get(action)
        if command is None:
            return ActionResult.UNSUPPORTED
        return self._client.command(f"[con_id={int(raw_id)}] {command}")


class SwayWorkspaceService(WorkspaceService):
    def __init__(self, *, client: SwayIpcClient, windows: SwayWindowService) -> None:
        self._client = client
        self._windows = windows

    def start(self) -> None:
        """Window service owns subscriptions."""

    def stop(self) -> None:
        """Window service owns subscriptions."""

    def list_workspaces(self) -> Sequence[WorkspaceSnapshot]:
        try:
            rows = self._client.query(1)
        except (OSError, ValueError):
            return ()
        if not isinstance(rows, list):
            return ()
        return tuple(
            WorkspaceSnapshot(
                str(row.get("id")),
                index + 1,
                str(row.get("name", "")),
                row.get("focused") is True,
            )
            for index, row in enumerate(rows)
            if isinstance(row, Mapping)
        )

    def active_workspace(self) -> WorkspaceSnapshot | None:
        return next((row for row in self.list_workspaces() if row.active), None)

    def activate(self, workspace_id: str) -> ActionResult:
        row = next(
            (row for row in self.list_workspaces() if row.id == workspace_id), None
        )
        if row is None:
            return ActionResult.NOT_FOUND
        # JSON quoting is the i3 command parser's quoted-string syntax.
        return self._client.command(f"workspace {json.dumps(row.name)}")

    def watch_active_workspace(self, on_change: Callable[[], None]) -> object:
        return self._windows.watch(on_change)

    def unwatch_active_workspace(self, handle: object) -> None:
        self._windows.unwatch(handle)
