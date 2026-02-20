"""Drag-and-drop: internal reordering + external .desktop file drops."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse, unquote

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf  # noqa: E402

if TYPE_CHECKING:
    from docking.dock_window import DockWindow
    from docking.dock_model import DockModel
    from docking.config import Config
    from docking.dock_renderer import DockRenderer
    from docking.theme import Theme
    from docking.launcher import Launcher

# DnD targets
_DOCK_ITEM_TARGET = Gtk.TargetEntry.new("dock-item-index", Gtk.TargetFlags.SAME_WIDGET, 0)
_URI_TARGET = Gtk.TargetEntry.new("text/uri-list", 0, 1)


class DnDHandler:
    """Manages drag-and-drop reordering and external .desktop drops."""

    def __init__(
        self,
        window: DockWindow,
        model: DockModel,
        config: Config,
        renderer: DockRenderer,
        theme: Theme,
        launcher: Launcher,
    ) -> None:
        self._window = window
        self._model = model
        self._config = config
        self._renderer = renderer
        self._theme = theme
        self._launcher = launcher

        self.drag_index: int = -1
        self._drag_from: int = -1

        self._setup_dnd()

    def _setup_dnd(self) -> None:
        """Configure GTK drag-and-drop on the drawing area."""
        da = self._window.drawing_area

        da.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK,
            [_DOCK_ITEM_TARGET],
            Gdk.DragAction.MOVE,
        )
        # No DestDefaults — we handle motion/drop manually to support
        # both internal reorder and external URI drops without conflicts
        da.drag_dest_set(
            0,
            [_DOCK_ITEM_TARGET, _URI_TARGET],
            Gdk.DragAction.MOVE | Gdk.DragAction.COPY,
        )

        da.connect("drag-begin", self._on_drag_begin)
        da.connect("drag-motion", self._on_drag_motion)
        da.connect("drag-drop", self._on_drag_drop)
        da.connect("drag-end", self._on_drag_end)
        da.connect("drag-data-received", self._on_drag_data_received)
        da.connect("drag-leave", self._on_drag_leave)

    def _on_drag_begin(self, widget: Gtk.DrawingArea, context: Gdk.DragContext) -> None:
        """Identify which item is being dragged and set the drag icon."""
        from docking.zoom import compute_layout
        items = self._model.visible_items()
        layout = compute_layout(
            items, self._config, self._window.cursor_x,
            item_padding=self._theme.item_padding,
            h_padding=self._theme.h_padding,
        )

        for i, li in enumerate(layout):
            icon_w = li.scale * self._config.icon_size
            if li.x <= self._window.cursor_x <= li.x + icon_w:
                self._drag_from = i
                self.drag_index = i

                item = items[i]
                if item.icon:
                    icon_size = int(self._config.icon_size * 1.2)
                    scaled = item.icon.scale_simple(
                        icon_size, icon_size, GdkPixbuf.InterpType.BILINEAR
                    )
                    if scaled:
                        Gtk.drag_set_icon_pixbuf(context, scaled, icon_size // 2, icon_size // 2)
                return

    def _on_drag_motion(
        self, widget: Gtk.DrawingArea, context: Gdk.DragContext,
        x: int, y: int, time: int,
    ) -> bool:
        """Update drop position as user drags."""
        # Reveal dock when dragging over it (enter-notify doesn't fire during DnD)
        if self._window._autohide:
            self._window._autohide.on_mouse_enter()
        if self._drag_from < 0:
            # External drag — just accept it
            Gdk.drag_status(context, Gdk.DragAction.COPY, time)
            return True

        from docking.zoom import compute_layout
        items = self._model.visible_items()
        layout = compute_layout(
            items, self._config, -1.0,
            item_padding=self._theme.item_padding,
            h_padding=self._theme.h_padding,
        )

        new_index = len(layout) - 1
        for i, li in enumerate(layout):
            center = li.x + self._config.icon_size / 2
            if x < center:
                new_index = i
                break

        if new_index != self.drag_index:
            self._model.reorder_visible(self.drag_index, new_index)
            self.drag_index = new_index

        Gdk.drag_status(context, Gdk.DragAction.MOVE, time)

        widget.queue_draw()
        return True

    def _on_drag_drop(
        self, widget: Gtk.DrawingArea, context: Gdk.DragContext,
        x: int, y: int, time: int,
    ) -> bool:
        """Request data for external drops, finalize internal drops."""
        target = widget.drag_dest_find_target(context, None)
        if target:
            widget.drag_get_data(context, target, time)
            return True
        return False

    def _on_drag_data_received(
        self, widget: Gtk.DrawingArea, context: Gdk.DragContext,
        x: int, y: int, selection: Gtk.SelectionData,
        info: int, time: int,
    ) -> None:
        """Handle internal reorder completion and external .desktop drops."""
        # Internal reorder — already handled during drag-motion
        if self._drag_from >= 0:
            Gtk.drag_finish(context, True, False, time)
            return

        # External drop — process URIs
        uris = selection.get_uris()
        if not uris:
            text = selection.get_text()
            if text:
                uris = [line.strip() for line in text.splitlines() if line.strip()]

        added = False
        for uri in uris:
            desktop_id = self._uri_to_desktop_id(uri)
            if desktop_id and not self._model.find_by_desktop_id(desktop_id):
                resolved = self._launcher.resolve(desktop_id)
                if resolved:
                    self._config.pinned.append(desktop_id)
                    icon_size = int(self._config.icon_size * self._config.zoom_percent)
                    icon = self._launcher.load_icon(resolved.icon_name, icon_size)
                    from docking.dock_model import DockItem
                    self._model._pinned.append(DockItem(
                        desktop_id=desktop_id,
                        name=resolved.name,
                        icon_name=resolved.icon_name,
                        wm_class=resolved.wm_class,
                        is_pinned=True,
                        icon=icon,
                    ))
                    added = True

        if added:
            self._config.save()
            self._model._notify()

        Gtk.drag_finish(context, added, False, time)

    def _on_drag_leave(self, widget: Gtk.DrawingArea, context: Gdk.DragContext, time: int) -> None:
        """Hide dock when drag leaves (leave-notify doesn't fire during DnD)."""
        if self._window._autohide:
            self._window._autohide.on_mouse_leave()

    def _on_drag_end(self, widget: Gtk.DrawingArea, context: Gdk.DragContext) -> None:
        """Clean up drag state and persist order."""
        self.drag_index = -1
        self._drag_from = -1
        self._config.save()
        widget.queue_draw()

    @staticmethod
    def _uri_to_desktop_id(uri: str) -> str | None:
        """Extract a .desktop ID from a file URI or path."""
        parsed = urlparse(uri)
        if parsed.scheme == "file":
            path = Path(unquote(parsed.path))
        elif parsed.scheme == "" and uri.endswith(".desktop"):
            path = Path(uri)
        else:
            return None

        if not path.name.endswith(".desktop"):
            return None

        return path.name
