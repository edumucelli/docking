"""Workspaces applet behavior and GTK/Wnck wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Wnck", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk, Wnck  # noqa: E402

from docking.applets.base import Applet
from docking.applets.identity import AppletId
from docking.log import get_logger, with_context

from .render import _render_grid
from .state import (
    active_workspace_number,
    next_workspace_index,
    workspace_count,
    workspace_label,
)

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="workspaces"), applet_id=str(AppletId.WORKSPACES))


class WorkspacesApplet(Applet):
    """Shows workspace grid icon, click cycles, scroll switches."""

    id = AppletId.WORKSPACES
    name = "Workspaces"
    icon_name = "preferences-desktop-workspaces"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._screen: Wnck.Screen | None = None
        self._signal_id: int = 0
        self._last_logged_state: tuple[int, int, str, str] | None = None
        super().__init__(icon_size, config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        screen = self._screen or Wnck.Screen.get_default()
        if screen:
            screen.force_update()
        workspaces = screen.get_workspaces() if screen else []
        active = screen.get_active_workspace() if screen else None
        active_num = active_workspace_number(
            active_number=active.get_number() if active else None
        )
        count = workspace_count(count=len(workspaces) if workspaces else None)
        active_name = (active.get_name() or "").strip() if active else ""

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _render_grid(cr=cr, size=size, count=count, active_num=active_num)

        if hasattr(self, "item"):
            label = (
                workspace_label(
                    name=active_name or None,
                    number=active_num,
                )
                if active_num >= 0
                else "Desktop"
            )
            self.item.name = label
            log_state = (count, active_num, active_name, label)
            if log_state != self._last_logged_state:
                self._last_logged_state = log_state
                _log.bind(action="create_icon").debug(
                    "workspace state count=%s active_num=%s active_name=%r label=%r",
                    count,
                    active_num,
                    active_name,
                    label,
                )

        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    def on_clicked(self) -> None:
        """Cycle to next workspace."""
        screen = self._screen
        if screen is None:
            return
        screen.force_update()
        active = screen.get_active_workspace()
        if active is None:
            return
        count = screen.get_workspace_count()
        next_num = next_workspace_index(
            current=active.get_number(), count=count, delta=1
        )
        _log.bind(action="on_clicked").debug(
            "workspace switch current=%s next=%s count=%s",
            active.get_number(),
            next_num,
            count,
        )
        target = screen.get_workspace(next_num)
        if target:
            target.activate(Gtk.get_current_event_time() or 0)

    def on_scroll(self, direction_up: bool) -> None:
        """Switch workspace on scroll."""
        screen = self._screen
        if screen is None:
            return
        screen.force_update()
        active = screen.get_active_workspace()
        if active is None:
            return
        count = screen.get_workspace_count()
        delta = -1 if direction_up else 1
        next_num = next_workspace_index(
            current=active.get_number(),
            count=count,
            delta=delta,
        )
        _log.bind(action="on_scroll").debug(
            "workspace switch direction_up=%s current=%s next=%s count=%s",
            direction_up,
            active.get_number(),
            next_num,
            count,
        )
        target = screen.get_workspace(next_num)
        if target:
            target.activate(Gtk.get_current_event_time() or 0)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        screen = self._screen
        if screen is None:
            return []
        screen.force_update()
        workspaces = screen.get_workspaces()
        active = screen.get_active_workspace()
        active_num = active_workspace_number(
            active_number=active.get_number() if active else None
        )

        items: list[Gtk.MenuItem] = []
        first: Gtk.RadioMenuItem | None = None
        for ws in workspaces:
            label = workspace_label(name=ws.get_name(), number=ws.get_number())
            radio = Gtk.RadioMenuItem(label=label)
            if first:
                radio.join_group(first)
            else:
                first = radio
            if ws.get_number() == active_num:
                radio.set_active(True)
            radio.connect("activate", self._on_workspace_activate, ws)
            items.append(radio)
        return items

    def _on_workspace_activate(
        self, _widget: Gtk.RadioMenuItem, workspace: Wnck.Workspace
    ) -> None:
        _log.bind(action="menu_activate").debug(
            "menu workspace activate number=%s name=%r",
            workspace.get_number(),
            workspace.get_name(),
        )
        workspace.activate(Gtk.get_current_event_time() or 0)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify)
        self._screen = Wnck.Screen.get_default()
        if self._screen:
            self._screen.force_update()
            self._signal_id = self._screen.connect(
                "active-workspace-changed", self._on_workspace_changed
            )
            self.refresh_icon()

    def stop(self) -> None:
        if self._screen and self._signal_id:
            self._screen.disconnect(self._signal_id)
            self._signal_id = 0
        super().stop()

    def _on_workspace_changed(self, _screen: Wnck.Screen, *_args: Any) -> None:
        _log.bind(action="workspace_changed").debug("received active-workspace-changed")
        if self._screen:
            self._screen.force_update()
        self.refresh_icon()
