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

"""Secondary dock UI interactions composed outside the dock window.

`DockWindow` owns raw GTK events and geometry. This coordinator owns the
secondary UI consequences of those events: context menus and folder-stack
popups. Keeping this layer between the window shell and `MenuHandler` lets the
menu builder focus on menu contents instead of becoming part of the window's
event contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk

if TYPE_CHECKING:
    from docking.core.items import DockItem
    from docking.core.position import Position
    from docking.ui.folder.stack import FolderStackController
    from docking.ui.geometry import DockGeometryFrame
    from docking.ui.menu import MenuHandler


@dataclass(frozen=True, slots=True)
class FolderStackAnchor:
    """Screen-space anchor for a folder-stack popup."""

    x: int
    y: int
    icon_w: int
    position: Position


class DockInteractions:
    """Coordinate menu and folder-stack interactions for the dock shell."""

    def __init__(
        self,
        *,
        menu: MenuHandler,
        folder_stack: FolderStackController,
    ) -> None:
        self._menu = menu
        self._folder_stack = folder_stack

    def show_context_menu(
        self,
        *,
        event: Gdk.EventButton,
        cursor_main: float,
        frame: DockGeometryFrame,
        force_background: bool = False,
    ) -> None:
        """Show the dock context menu using the event's current geometry frame."""
        self._folder_stack.close()
        self._menu.show(
            event=event,
            cursor_main=cursor_main,
            frame=frame,
            force_background=force_background,
        )

    def show_folder_stack(
        self,
        *,
        item: DockItem,
        anchor: FolderStackAnchor,
        toggle_if_same_item: bool = True,
    ) -> None:
        """Show a folder stack popup for the provided dock item."""
        self._folder_stack.show(
            item=item,
            anchor_x=anchor.x,
            anchor_y=anchor.y,
            icon_w=anchor.icon_w,
            position=anchor.position,
            toggle_if_same_item=toggle_if_same_item,
        )

    def close_folder_stack_for_item(self, desktop_id: str) -> None:
        """Close the stack only when it currently belongs to the item."""
        if self._folder_stack.open_item_id() == desktop_id:
            self._folder_stack.close()

    def close_folder_stack_unless_target(self, hovered_item: DockItem | None) -> None:
        """Close the visible stack when the pointer leaves its source folder."""
        open_item_id = self._folder_stack.open_item_id()
        if open_item_id is None:
            return
        if hovered_item is not None and hovered_item.desktop_id == open_item_id:
            return
        self._folder_stack.close()

    def prewarm_folder_stack(self, item: DockItem) -> None:
        """Queue a single folder stack layout warm-up."""
        self._folder_stack.schedule_prewarm(item)

    def prewarm_visible_folder_stacks(self, items: Sequence[DockItem]) -> None:
        """Warm folder stack layouts for all visible folder items."""
        self._folder_stack.schedule_visible_prewarm(items)

    def folder_stack_item_id(self) -> str | None:
        """Return the desktop id that owns the currently visible folder stack."""
        return self._folder_stack.open_item_id()
