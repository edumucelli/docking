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

"""X11 workspace service backed by Wnck."""

from __future__ import annotations

from contextlib import suppress
from typing import NamedTuple

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Wnck", "3.0")
from gi.repository import Gtk, Wnck

from docking.platform.backends.base import (
    ActionResult,
    WorkspaceService,
    WorkspaceSnapshot,
)


class WorkspaceWatchHandle(NamedTuple):
    screen: Wnck.Screen
    signal_id: int


class WnckWorkspaceService(WorkspaceService):
    """WorkspaceService implementation backed by Wnck."""

    def start(self) -> None:
        """No service-level runtime loop is needed."""

    def stop(self) -> None:
        """No persistent service-level resources are held."""

    def list_workspaces(self) -> tuple[WorkspaceSnapshot, ...]:
        screen = Wnck.Screen.get_default()
        if screen is None:
            return ()
        screen.force_update()
        active = screen.get_active_workspace()
        active_number = active.get_number() if active else -1
        return tuple(
            WorkspaceSnapshot(
                id=str(workspace.get_number()),
                number=workspace.get_number(),
                name=(workspace.get_name() or "").strip(),
                active=workspace.get_number() == active_number,
            )
            for workspace in screen.get_workspaces()
        )

    def active_workspace(self) -> WorkspaceSnapshot | None:
        screen = Wnck.Screen.get_default()
        if screen is None:
            return None
        screen.force_update()
        workspace = screen.get_active_workspace()
        if workspace is None:
            return None
        return WorkspaceSnapshot(
            id=str(workspace.get_number()),
            number=workspace.get_number(),
            name=(workspace.get_name() or "").strip(),
            active=True,
        )

    def activate(self, workspace_id: str) -> ActionResult:
        screen = Wnck.Screen.get_default()
        if screen is None:
            return ActionResult.NOT_FOUND
        try:
            index = int(workspace_id)
        except ValueError:
            return ActionResult.NOT_FOUND
        screen.force_update()
        target = screen.get_workspace(index)
        if target is None:
            return ActionResult.NOT_FOUND
        target.activate(Gtk.get_current_event_time() or 0)
        return ActionResult.OK

    def watch_active_workspace(self, on_change) -> WorkspaceWatchHandle | None:
        screen = Wnck.Screen.get_default()
        if screen is None:
            return None
        screen.force_update()
        signal_id = screen.connect(
            "active-workspace-changed",
            lambda *_args: on_change(),
        )
        return WorkspaceWatchHandle(screen=screen, signal_id=signal_id)

    def unwatch_active_workspace(self, handle: object) -> None:
        if not isinstance(handle, WorkspaceWatchHandle):
            return
        with suppress(Exception):
            handle.screen.disconnect(handle.signal_id)
