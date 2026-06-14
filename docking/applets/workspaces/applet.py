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

"""Workspaces applet behavior and GTK wiring."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk

from docking.applets.base import Applet
from docking.applets.menu import menu_sections, radio_menu_items
from docking.applets.services import AppletServices
from docking.applets.workspaces import meta
from docking.i18n import _
from docking.log import get_logger, with_context
from docking.platform.backends.base import WorkspaceService, WorkspaceSnapshot

from .render import _render_grid
from .state import (
    next_workspace_index,
    workspace_count,
    workspace_label,
)

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="workspaces"), applet_id=meta.id)


class WorkspacesApplet(Applet):
    """Shows workspace grid icon, click cycles, scroll switches."""

    id = meta.id
    name = _("Workspaces")
    icon_name = "preferences-desktop-workspaces"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._workspace_service: WorkspaceService | None = None
        self._watch_handle: object | None = None
        self._last_logged_state: tuple[int, int, str, str] | None = None
        self._workspace_count: int = 1
        self._active_num: int = -1
        self._active_name: str = ""
        super().__init__(icon_size, config)
        self.present()

    def set_services(self, services: AppletServices) -> None:
        if self._workspace_service is not None and self._watch_handle is not None:
            self._workspace_service.unwatch_active_workspace(self._watch_handle)
            self._watch_handle = None
        self._workspace_service = services.workspaces
        if self._workspace_service is not None and self._notify is not None:
            self._watch_handle = self._workspace_service.watch_active_workspace(
                self._on_workspace_changed
            )
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        workspaces = self._workspace_snapshots()
        active = self._active_workspace(workspaces=workspaces)
        active_num = active.number if active is not None else -1
        count = workspace_count(count=len(workspaces) if workspaces else None)
        active_name = active.name if active is not None else ""
        self._workspace_count = count
        self._active_num = active_num
        self._active_name = active_name

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _render_grid(cr=cr, size=size, count=count, active_num=active_num)

        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    def refresh_tooltip(self) -> None:
        label = (
            workspace_label(
                name=self._active_name or None,
                number=self._active_num,
            )
            if self._active_num >= 0
            else _("Desktop")
        )
        self.item.name = label
        log_state = (
            self._workspace_count,
            self._active_num,
            self._active_name,
            label,
        )
        if log_state != self._last_logged_state:
            self._last_logged_state = log_state
            log.bind(action="refresh_tooltip").debug(
                "workspace state count=%s active_num=%s active_name=%r label=%r",
                self._workspace_count,
                self._active_num,
                self._active_name,
                label,
            )

    def on_clicked(self) -> None:
        """Cycle to next workspace."""
        service = self._workspace_service
        if service is None:
            return
        workspaces = self._workspace_snapshots()
        active = self._active_workspace(workspaces=workspaces)
        if active is None:
            return
        count = len(workspaces)
        next_num = next_workspace_index(current=active.number, count=count, delta=1)
        log.bind(action="on_clicked").debug(
            "workspace switch current=%s next=%s count=%s",
            active.number,
            next_num,
            count,
        )
        if 0 <= next_num < len(workspaces):
            service.activate(workspaces[next_num].id)

    def on_scroll(self, direction_up: bool) -> None:
        """Switch workspace on scroll."""
        service = self._workspace_service
        if service is None:
            return
        workspaces = self._workspace_snapshots()
        active = self._active_workspace(workspaces=workspaces)
        if active is None:
            return
        count = len(workspaces)
        delta = -1 if direction_up else 1
        next_num = next_workspace_index(
            current=active.number,
            count=count,
            delta=delta,
        )
        log.bind(action="on_scroll").debug(
            "workspace switch direction_up=%s current=%s next=%s count=%s",
            direction_up,
            active.number,
            next_num,
            count,
        )
        if 0 <= next_num < len(workspaces):
            service.activate(workspaces[next_num].id)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        service = self._workspace_service
        if service is None:
            return []
        workspaces = self._workspace_snapshots()
        active = self._active_workspace(workspaces=workspaces)
        active_id = active.id if active else ""

        return menu_sections(
            display=radio_menu_items(
                choices=tuple(
                    (
                        workspace_label(name=ws.name, number=ws.number),
                        ws.id,
                    )
                    for ws in workspaces
                ),
                active_value=active_id,
                is_active=lambda workspace_id: workspace_id == active_id,
                on_selected=lambda widget, value: self._on_workspace_activate(
                    widget,
                    value,
                ),
                gtk=Gtk,
            ),
            gtk=Gtk,
        )

    def _on_workspace_activate(
        self, _widget: Gtk.RadioMenuItem, workspace_id: str
    ) -> None:
        log.bind(action="menu_activate").debug(
            "menu workspace activate id=%s",
            workspace_id,
        )
        if self._workspace_service is not None:
            self._workspace_service.activate(workspace_id)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify)
        if self._workspace_service is not None and self._watch_handle is None:
            self._watch_handle = self._workspace_service.watch_active_workspace(
                self._on_workspace_changed
            )
        self.present()

    def stop(self) -> None:
        if self._workspace_service is not None and self._watch_handle is not None:
            self._workspace_service.unwatch_active_workspace(self._watch_handle)
            self._watch_handle = None
        super().stop()

    def _on_workspace_changed(self) -> None:
        log.bind(action="workspace_changed").debug("received active-workspace-changed")
        self.present()

    def _workspace_snapshots(self) -> tuple[WorkspaceSnapshot, ...]:
        if self._workspace_service is None:
            return ()
        return tuple(self._workspace_service.list_workspaces())

    def _active_workspace(
        self, *, workspaces: tuple[WorkspaceSnapshot, ...]
    ) -> WorkspaceSnapshot | None:
        for workspace in workspaces:
            if workspace.active:
                return workspace
        if self._workspace_service is None:
            return None
        return self._workspace_service.active_workspace()
