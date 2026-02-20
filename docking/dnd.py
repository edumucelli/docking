"""Drag-and-drop reordering within the dock."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf  # noqa: E402

if TYPE_CHECKING:
    from docking.dock_window import DockWindow
    from docking.dock_model import DockModel
    from docking.config import Config
    from docking.dock_renderer import DockRenderer
    from docking.theme import Theme

# Internal DnD target
_DOCK_ITEM_TARGET = Gtk.TargetEntry.new("dock-item-index", Gtk.TargetFlags.SAME_WIDGET, 0)


class DnDHandler:
    """Manages drag-and-drop reordering of dock items."""

    def __init__(
        self,
        window: DockWindow,
        model: DockModel,
        config: Config,
        renderer: DockRenderer,
        theme: Theme,
    ) -> None:
        self._window = window
        self._model = model
        self._config = config
        self._renderer = renderer
        self._theme = theme

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
        da.drag_dest_set(
            Gtk.DestDefaults.MOTION | Gtk.DestDefaults.DROP,
            [_DOCK_ITEM_TARGET],
            Gdk.DragAction.MOVE,
        )

        da.connect("drag-begin", self._on_drag_begin)
        da.connect("drag-motion", self._on_drag_motion)
        da.connect("drag-drop", self._on_drag_drop)
        da.connect("drag-end", self._on_drag_end)

    def _on_drag_begin(self, widget: Gtk.DrawingArea, context: Gdk.DragContext) -> None:
        """Identify which item is being dragged and set the drag icon."""
        from docking.zoom import compute_layout
        items = self._model.visible_items()
        layout = compute_layout(
            items, self._config, self._window.cursor_x,
            item_padding=self._theme.item_padding,
            h_padding=self._theme.h_padding,
        )

        # Find the item under cursor
        for i, li in enumerate(layout):
            icon_w = li.scale * self._config.icon_size
            if li.x <= self._window.cursor_x <= li.x + icon_w:
                self._drag_from = i
                self.drag_index = i

                # Set drag icon
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
        if self._drag_from < 0:
            return False

        from docking.zoom import compute_layout
        items = self._model.visible_items()
        # Compute layout without zoom (cursor_x = -1) for stable positions
        layout = compute_layout(
            items, self._config, -1.0,
            item_padding=self._theme.item_padding,
            h_padding=self._theme.h_padding,
        )

        # Find drop position
        new_index = len(layout) - 1
        for i, li in enumerate(layout):
            center = li.x + self._config.icon_size / 2
            if x < center:
                new_index = i
                break

        if new_index != self.drag_index:
            pinned_count = len([it for it in items if it.is_pinned])
            # Only allow reorder within pinned items
            if self._drag_from < pinned_count and new_index < pinned_count:
                self._model.reorder(self.drag_index, new_index)
                self.drag_index = new_index

        widget.queue_draw()
        return True

    def _on_drag_drop(
        self, widget: Gtk.DrawingArea, context: Gdk.DragContext,
        x: int, y: int, time: int,
    ) -> bool:
        """Finalize the drop."""
        Gtk.drag_finish(context, True, False, time)
        return True

    def _on_drag_end(self, widget: Gtk.DrawingArea, context: Gdk.DragContext) -> None:
        """Clean up drag state and persist order."""
        self.drag_index = -1
        self._drag_from = -1
        self._config.save()
        widget.queue_draw()
