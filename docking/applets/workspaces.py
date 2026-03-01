"""Workspaces applet -- workspace switcher with visual grid icon."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Wnck", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk, Wnck  # noqa: E402

from docking.applets.base import Applet
from docking.applets.ids import AppletId
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config

_log = get_logger(name="workspaces")


class WorkspacesApplet(Applet):
    """Shows workspace grid icon, click cycles, scroll switches.

    Icon renders a grid of rectangles with the active workspace highlighted.
    Right-click menu lists all workspaces as radio items.
    """

    id = AppletId.WORKSPACES
    name = "Workspaces"
    icon_name = "preferences-desktop-workspaces"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._screen: Wnck.Screen | None = None
        self._signal_id: int = 0
        super().__init__(icon_size, config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        screen = self._screen or Wnck.Screen.get_default()
        workspaces = screen.get_workspaces() if screen else []
        active = screen.get_active_workspace() if screen else None
        active_num = active.get_number() if active else -1
        count = len(workspaces) if workspaces else 4

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _render_grid(cr=cr, size=size, count=count, active_num=active_num)

        if hasattr(self, "item"):
            name = active.get_name() if active else "Desktop"
            self.item.name = name

        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    def on_clicked(self) -> None:
        """Cycle to next workspace."""
        screen = self._screen
        if screen is None:
            return
        active = screen.get_active_workspace()
        if active is None:
            return
        count = screen.get_workspace_count()
        next_num = (active.get_number() + 1) % count
        target = screen.get_workspace(next_num)
        if target:
            target.activate(Gtk.get_current_event_time() or 0)

    def on_scroll(self, direction_up: bool) -> None:
        """Switch workspace on scroll."""
        screen = self._screen
        if screen is None:
            return
        active = screen.get_active_workspace()
        if active is None:
            return
        count = screen.get_workspace_count()
        delta = -1 if direction_up else 1
        next_num = (active.get_number() + delta) % count
        target = screen.get_workspace(next_num)
        if target:
            target.activate(Gtk.get_current_event_time() or 0)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        screen = self._screen
        if screen is None:
            return []
        workspaces = screen.get_workspaces()
        active = screen.get_active_workspace()
        active_num = active.get_number() if active else -1

        items: list[Gtk.MenuItem] = []
        first: Gtk.RadioMenuItem | None = None
        for ws in workspaces:
            label = ws.get_name() or f"Workspace {ws.get_number() + 1}"
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
        self.refresh_icon()


def _render_grid(cr: cairo.Context, size: int, count: int, active_num: int) -> None:
    """Draw a grid of workspace rectangles with the active one highlighted."""
    if count <= 0:
        return

    # Grid layout: prefer 2xN
    cols = 2 if count > 1 else 1
    rows = (count + cols - 1) // cols

    margin = size * 0.12
    gap = size * 0.06
    grid_w = size - 2 * margin
    grid_h = size - 2 * margin
    cell_w = (grid_w - (cols - 1) * gap) / cols
    cell_h = (grid_h - (rows - 1) * gap) / rows
    radius = size * 0.04

    for idx in range(count):
        row = idx // cols
        col = idx % cols
        x = margin + col * (cell_w + gap)
        y = margin + row * (cell_h + gap)

        # Rounded rectangle
        cr.new_sub_path()
        cr.arc(x + cell_w - radius, y + radius, radius, -1.5708, 0)
        cr.arc(x + cell_w - radius, y + cell_h - radius, radius, 0, 1.5708)
        cr.arc(x + radius, y + cell_h - radius, radius, 1.5708, 3.1416)
        cr.arc(x + radius, y + radius, radius, 3.1416, 4.7124)
        cr.close_path()

        if idx == active_num:
            cr.set_source_rgba(0.3, 0.6, 1.0, 0.9)
        else:
            cr.set_source_rgba(0.7, 0.7, 0.7, 0.5)
        cr.fill()
