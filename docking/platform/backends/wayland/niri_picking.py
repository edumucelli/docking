"""Niri-owned interactive selection with live window/PID validation."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from typing import TYPE_CHECKING

from gi.repository import Gio, GLib

from docking.platform.backends.base import (
    ActionResult,
    WindowId,
    WindowPickService,
    WindowSnapshot,
)
from docking.platform.backends.wayland.niri_ipc import (
    _niri_socket_path,
    _niri_window_id,
    _parse_response,
)

if TYPE_CHECKING:
    from docking.platform.backends.wayland.niri_ipc import NiriWindowService


class NiriWindowPickService(WindowPickService):
    """Use PickWindow even when tiled windows have no screen coordinates."""

    interactive = True

    def __init__(self, *, windows: NiriWindowService, socket_path: str) -> None:
        self._windows = windows
        self._socket_path = socket_path
        self._cancellable: Gio.Cancellable | None = None
        self._loop: GLib.MainLoop | None = None
        self._selected_pids: dict[WindowId, int | None] = {}

    def start(self) -> None:
        """Connect only while the user is selecting a window."""

    def stop(self) -> None:
        if self._cancellable is not None:
            self._cancellable.cancel()
        if self._loop is not None:
            self._loop.quit()
        self._selected_pids.clear()

    def select_window(self) -> WindowSnapshot | None:
        if self._cancellable is not None:
            return None
        self._selected_pids.clear()
        cancellable = Gio.Cancellable()
        loop = GLib.MainLoop()
        self._cancellable, self._loop = cancellable, loop
        replies: list[bytes] = []
        connections: list[Gio.SocketConnection] = []

        def read_reply(source, result, *_unused):
            try:
                line, _length = source.read_line_finish(result)
                if line is not None and not cancellable.is_cancelled():
                    replies.append(line)
            except GLib.Error:
                pass
            loop.quit()

        def connected(source, result, *_unused):
            try:
                connection = source.connect_finish(result)
                connections.append(connection)
                if cancellable.is_cancelled():
                    connection.close(None)
                    return
                connection.get_output_stream().write_all(b'"PickWindow"\n', cancellable)
                connection.get_socket().shutdown(False, True)
                reader = Gio.DataInputStream.new(connection.get_input_stream())
                reader.read_line_async(GLib.PRIORITY_DEFAULT, cancellable, read_reply)
            except GLib.Error:
                loop.quit()

        timer = 0

        def expire():
            nonlocal timer
            timer = 0
            cancellable.cancel()
            loop.quit()
            return False

        client = Gio.SocketClient()
        client.set_timeout(60)
        client.connect_async(
            Gio.UnixSocketAddress.new(self._socket_path), cancellable, connected
        )
        timer = GLib.timeout_add_seconds(60, expire)
        try:
            loop.run()
        finally:
            cancellable.cancel()
            if timer:
                GLib.source_remove(timer)
            for connection in connections:
                with suppress(GLib.Error):
                    connection.close(None)
            self._cancellable = self._loop = None
        if not replies:
            return None
        try:
            reply = _parse_response(replies[0])
        except json.JSONDecodeError:
            return None
        ok = reply.get("Ok")
        picked = ok.get("PickedWindow") if isinstance(ok, dict) else None
        window_id = picked.get("id") if isinstance(picked, dict) else None
        if type(window_id) is not int or window_id < 0:
            return None
        from docking.platform.backends.base import DisplayServer

        window = self._windows.live_window(
            WindowId(backend=DisplayServer.WAYLAND, value=str(window_id))
        )
        if window is not None:
            self._selected_pids = {window.id: window.pid}
        return window

    def pick_window_at(self, *, x: int, y: int) -> WindowSnapshot | None:
        # Niri owns hit testing; its IPC does not provide a point-query API.
        return None

    def pid_for(self, window_id: WindowId) -> int | None:
        if _niri_window_id(window_id) is None:
            return None
        row = self._windows.live_window(window_id)
        pid = row.pid if row is not None else None
        return pid if pid is not None and pid > 1 and pid != os.getpid() else None

    def kill(self, window_id: WindowId) -> ActionResult:
        from docking.applets.windowkiller.state import kill_pid

        selected_pid = self._selected_pids.pop(window_id, None)
        pid = self.pid_for(window_id)
        if pid is None or selected_pid != pid:
            return ActionResult.NOT_FOUND
        return ActionResult.OK if kill_pid(pid) else ActionResult.FAILED


def load_niri_window_pick_service(
    *, windows: NiriWindowService
) -> NiriWindowPickService | None:
    path = _niri_socket_path()
    if path is None or not path.exists():
        return None
    return NiriWindowPickService(windows=windows, socket_path=str(path))
