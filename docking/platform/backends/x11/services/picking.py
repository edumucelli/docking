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

"""X11 window-picking service for applets."""

from __future__ import annotations

import gi

gi.require_version("Wnck", "3.0")
from gi.repository import Wnck

from docking.applets.windowkiller.state import kill_pid
from docking.log import get_logger
from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    WindowId,
    WindowPickService,
    WindowSnapshot,
)

log = get_logger(name="x11_picking")


class WnckWindowPickService(WindowPickService):
    """WindowPickService backed by Wnck global window inspection."""

    def start(self) -> None:
        """No service-level runtime loop is needed."""

    def stop(self) -> None:
        """No persistent resources are held."""

    def pick_window_at(self, *, x: int, y: int) -> WindowSnapshot | None:
        screen = Wnck.Screen.get_default()
        if screen is None:
            return None
        screen.force_update()
        window = self._window_at(screen=screen, x=x, y=y)
        if window is None:
            return None
        xid = self._safe_xid(window=window)
        if xid is None:
            return None
        return WindowSnapshot(
            id=WindowId.x11(xid),
            desktop_id="",
            title=self._safe_name(window=window),
            can_close=True,
        )

    def pid_for(self, window_id: WindowId) -> int | None:
        window = self._window_for_id(window_id=window_id)
        if window is None:
            return None
        try:
            pid = int(window.get_pid())
        except Exception as exc:
            log.debug("Failed to read picked window PID: %s", exc)
            return None
        return pid if pid > 0 else None

    def kill(self, window_id: WindowId) -> ActionResult:
        pid = self.pid_for(window_id=window_id)
        if pid is None:
            return ActionResult.NOT_FOUND
        return ActionResult.OK if kill_pid(pid=pid) else ActionResult.FAILED

    def _window_for_id(self, *, window_id: WindowId) -> Wnck.Window | None:
        if window_id.backend is not DisplayServer.X11:
            return None
        try:
            xid = int(window_id.value)
        except (TypeError, ValueError):
            return None
        screen = Wnck.Screen.get_default()
        if screen is None:
            return None
        screen.force_update()
        for window in screen.get_windows():
            if self._safe_xid(window=window) == xid:
                return window
        return None

    def _window_at(self, screen: Wnck.Screen, x: int, y: int) -> Wnck.Window | None:
        """Find the topmost normal window containing (x, y)."""
        for win in reversed(screen.get_windows_stacked()):
            try:
                if win.get_window_type() != Wnck.WindowType.NORMAL:
                    continue
                if win.is_minimized():
                    continue
                wx, wy, ww, wh = win.get_geometry()
            except Exception as exc:
                log.debug("Failed to inspect candidate window during pick: %s", exc)
                continue
            if wx <= x < wx + ww and wy <= y < wy + wh:
                return win
        return None

    @staticmethod
    def _safe_xid(*, window: Wnck.Window) -> int | None:
        try:
            return int(window.get_xid())
        except Exception as exc:
            log.debug("Failed to read picked window XID: %s", exc)
            return None

    @staticmethod
    def _safe_name(*, window: Wnck.Window) -> str:
        try:
            return window.get_name() or "unknown"
        except Exception as exc:
            log.debug("Failed to read picked window name: %s", exc)
            return "unknown"
