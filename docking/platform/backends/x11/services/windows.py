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

"""X11 window-service facade backed by the existing Wnck WindowTracker."""

from __future__ import annotations

from typing import TYPE_CHECKING

from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    WindowId,
    WindowService,
)
from docking.platform.backends.x11.impl.window_tracker import WindowTracker

if TYPE_CHECKING:
    from gi.repository import Wnck


class X11WindowService(WindowTracker, WindowService):
    """WindowService adapter for the current X11/Wnck window tracker.

    WindowTracker owns the Wnck scanning and matching implementation; this class
    exposes that implementation through the backend-neutral WindowService
    contract used by the session backend.
    """

    def start(self) -> None:
        """Start X11 screen tracking if it has not already been initialized."""
        if self._screen is None:
            self._init_screen()

    def stop(self) -> None:
        """Release service state owned by the facade.

        Disconnect Wnck screen signals before dropping the screen reference so
        repeated start/stop cycles cannot duplicate callbacks.
        """
        self._disconnect_screen_signals()
        self._screen = None

    def activate(self, window_id: WindowId) -> ActionResult:
        """Activate one X11 window by backend-neutral window ID."""
        xid = self._xid_from_window_id(window_id)
        if xid is None:
            return ActionResult.UNSUPPORTED
        window = self._window_for_xid(xid=xid)
        if window is None:
            return ActionResult.NOT_FOUND
        self.activate_window(window=window)
        return ActionResult.OK

    def activate_most_recent(self, desktop_id: str) -> ActionResult:
        """Activate the most recent window for a desktop ID."""
        if self._screen is None:
            return ActionResult.UNSUPPORTED
        if not self._get_windows_for(desktop_id=desktop_id):
            return ActionResult.NOT_FOUND
        return super().activate_most_recent(desktop_id=desktop_id)

    def cycle(self, desktop_id: str) -> ActionResult:
        """Cycle windows for a desktop ID using the existing X11 policy."""
        if self._screen is None:
            return ActionResult.UNSUPPORTED
        if not self._get_windows_for(desktop_id=desktop_id):
            return ActionResult.NOT_FOUND
        self._cycle_windows(desktop_id=desktop_id)
        return ActionResult.OK

    def minimize_all(self, desktop_id: str) -> ActionResult:
        """Minimize all known windows for a desktop ID."""
        if self._screen is None:
            return ActionResult.UNSUPPORTED
        if not self._get_windows_for(desktop_id=desktop_id):
            return ActionResult.NOT_FOUND
        self._minimize_windows(desktop_id=desktop_id)
        return ActionResult.OK

    def close(self, window_id: WindowId) -> ActionResult:
        """Close one X11 window by backend-neutral window ID."""
        xid = self._xid_from_window_id(window_id)
        if xid is None:
            return ActionResult.UNSUPPORTED
        if self._window_for_xid(xid=xid) is None:
            return ActionResult.NOT_FOUND
        return super().close(window_id=window_id)

    def close_all(self, desktop_id: str) -> ActionResult:
        """Close all known windows for a desktop ID."""
        if self._screen is None:
            return ActionResult.UNSUPPORTED
        if not self._get_windows_for(desktop_id=desktop_id):
            return ActionResult.NOT_FOUND
        return super().close_all(desktop_id=desktop_id)

    def close_focused(self, desktop_id: str) -> ActionResult:
        """Close the active window for a desktop ID."""
        return super().close_focused(desktop_id=desktop_id)

    def toggle_focus(self, desktop_id: str) -> ActionResult:
        """Toggle focus/minimize behavior for a desktop ID."""
        return super().toggle_focus(desktop_id=desktop_id)

    def window_for_id(self, window_id: WindowId) -> Wnck.Window | None:
        """Resolve a live Wnck window by backend-neutral X11 window ID."""
        xid = self._xid_from_window_id(window_id)
        if xid is None:
            return None
        return self._window_for_xid(xid=xid)

    @staticmethod
    def _xid_from_window_id(window_id: WindowId) -> int | None:
        if window_id.backend is not DisplayServer.X11:
            return None
        try:
            return int(window_id.value)
        except (TypeError, ValueError):
            return None
