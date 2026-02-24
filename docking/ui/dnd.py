"""Drag-and-drop: internal reordering + external .desktop file drops."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse, unquote

from docking.log import get_logger

log = get_logger("dnd")

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf  # noqa: E402

from docking.core.position import Position, is_horizontal
from docking.core.zoom import compute_layout
from docking.ui.poof import show_poof
from docking.platform.model import DockItem

if TYPE_CHECKING:
    from docking.ui.dock_window import DockWindow
    from docking.platform.model import DockModel
    from docking.core.config import Config
    from docking.ui.renderer import DockRenderer
    from docking.core.theme import Theme
    from docking.platform.launcher import Launcher

DRAG_ICON_SCALE = 1.2  # dragged icon shown at this multiplier of icon_size

# DnD targets
_DOCK_ITEM_TARGET = Gtk.TargetEntry.new(
    "dock-item-index", Gtk.TargetFlags.SAME_WIDGET, 0
)
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
        self.drop_insert_index: int = -1  # for external drops: where to insert

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
        items = self._model.visible_items()
        local_cx = self._window.local_cursor_main()
        layout = compute_layout(
            items,
            self._config,
            local_cx,
            item_padding=self._theme.item_padding,
            h_padding=self._theme.h_padding,
        )

        offset = self._window.zoomed_main_offset(layout)
        horizontal = is_horizontal(self._config.pos)
        win_cx = self._window.cursor_x if horizontal else self._window.cursor_y
        log.debug(
            "drag-begin: win_cx=%.1f local_cx=%.1f offset=%.1f items=%d",
            win_cx,
            local_cx,
            offset,
            len(items),
        )
        for i, li in enumerate(layout):
            icon_width = li.scale * self._config.icon_size
            left = li.x + offset
            right = left + icon_width
            log.debug(
                "  item %d: left=%.1f right=%.1f (win_cx=%.1f)", i, left, right, win_cx
            )
            if left <= win_cx <= right:
                self._drag_from = i
                self.drag_index = i

                item = items[i]
                if item.icon:
                    icon_size = int(self._config.icon_size * DRAG_ICON_SCALE)
                    scaled = item.icon.scale_simple(
                        icon_size, icon_size, GdkPixbuf.InterpType.BILINEAR
                    )
                    if scaled:
                        Gtk.drag_set_icon_pixbuf(
                            context, scaled, icon_size // 2, icon_size // 2
                        )
                log.debug("  -> dragging item %d: %s", i, item.name)
                return
        log.debug("  -> no item matched")

    def _on_drag_motion(
        self,
        widget: Gtk.DrawingArea,
        context: Gdk.DragContext,
        x: int,
        y: int,
        time: int,
    ) -> bool:
        """Update drop position as user drags."""
        # GTK drag-and-drop event model quirk:
        #
        # During an active drag operation (user is dragging something),
        # GTK takes over mouse event delivery. The normal widget signals
        # that fire during regular mouse movement do NOT fire during DnD:
        #
        #   Normal hover:    enter-notify → motion-notify → leave-notify
        #   During DnD:      drag-motion  → (no enter/leave!) → drag-leave
        #
        # This means our autohide controller's on_mouse_enter() — which
        # is triggered by enter-notify-event — would never fire when the
        # user drags a .desktop file toward the dock to add it.
        #
        # To fix this, we explicitly call autohide.on_mouse_enter() from
        # the drag-motion handler, which IS delivered during DnD.
        if self._window.autohide:
            self._window.autohide.on_mouse_enter()
        main_coord = x if is_horizontal(self._config.pos) else y

        if self._drag_from < 0:
            # External drag — compute insert position for gap effect
            items = self._model.visible_items()
            layout = compute_layout(
                items,
                self._config,
                -1.0,
                item_padding=self._theme.item_padding,
                h_padding=self._theme.h_padding,
            )
            main_offset = self._window.zoomed_main_offset(layout)
            insert = len(layout)
            for i, li in enumerate(layout):
                center = li.x + main_offset + self._config.icon_size / 2
                if main_coord < center:
                    insert = i
                    break
            if insert != self.drop_insert_index:
                self.drop_insert_index = insert
                widget.queue_draw()
            Gdk.drag_status(context, Gdk.DragAction.COPY, time)
            return True

        items = self._model.visible_items()
        layout = compute_layout(
            items,
            self._config,
            -1.0,
            item_padding=self._theme.item_padding,
            h_padding=self._theme.h_padding,
        )

        main_offset = self._window.zoomed_main_offset(layout)
        new_index = len(layout) - 1
        for i, li in enumerate(layout):
            center = li.x + main_offset + self._config.icon_size / 2
            if main_coord < center:
                new_index = i
                break

        if new_index != self.drag_index:
            log.debug("drag-motion: reorder %d -> %d", self.drag_index, new_index)
            self._model.reorder_visible(self.drag_index, new_index)
            self.drag_index = new_index

        Gdk.drag_status(context, Gdk.DragAction.MOVE, time)

        widget.queue_draw()
        return True

    def _on_drag_drop(
        self,
        widget: Gtk.DrawingArea,
        context: Gdk.DragContext,
        _x: int,
        _y: int,
        time: int,
    ) -> bool:
        """Request data for external drops, finalize internal drops."""
        target = widget.drag_dest_find_target(context, None)
        log.debug(
            "drag-drop: drag_from=%d insert=%d target=%s",
            self._drag_from,
            self.drop_insert_index,
            target,
        )
        if target:
            widget.drag_get_data(context, target, time)
            return True
        return False

    def _on_drag_data_received(
        self,
        widget: Gtk.DrawingArea,
        context: Gdk.DragContext,
        _x: int,
        _y: int,
        selection: Gtk.SelectionData,
        _info: int,
        time: int,
    ) -> None:
        """Handle internal reorder completion and external .desktop drops."""
        # Internal reorder — already handled during drag-motion
        if self._drag_from >= 0:
            log.debug("drag-data-received: internal reorder complete")
            Gtk.drag_finish(context, True, False, time)
            return

        # External drop — process URIs
        insert_at = max(0, self.drop_insert_index)
        log.debug("drag-data-received: external drop, insert_at=%d", insert_at)
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
                    icon_size = int(self._config.icon_size * self._config.zoom_percent)
                    icon = self._launcher.load_icon(resolved.icon_name, icon_size)
                    item = DockItem(
                        desktop_id=desktop_id,
                        name=resolved.name,
                        icon_name=resolved.icon_name,
                        wm_class=resolved.wm_class,
                        is_pinned=True,
                        icon=icon,
                    )
                    # Insert at drop position
                    insert_at = min(insert_at, len(self._model.pinned_items))
                    self._model.pinned_items.insert(insert_at, item)
                    self._config.pinned.insert(insert_at, desktop_id)
                    insert_at += 1
                    added = True

        self.drop_insert_index = -1
        if added:
            self._config.save()
            self._model.sync_pinned_to_config()
            self._model.notify()

        Gtk.drag_finish(context, added, False, time)

    def _on_drag_leave(
        self, widget: Gtk.DrawingArea, _context: Gdk.DragContext, _time: int
    ) -> None:
        """Handle drag leaving the dock area.

        IMPORTANT: GTK fires events in this order during a drop:

          1. drag-motion   (mouse hovering over drop target)
          2. drag-leave    ← fires BEFORE the drop happens!
          3. drag-drop     (user releases the mouse button)
          4. drag-data-received  (dropped data is delivered)

        This means if we cleared drop_insert_index here in drag-leave,
        it would already be -1 by the time drag-data-received tries to
        read it to know WHERE to insert the dropped item.

        Therefore, we do NOT clear drop_insert_index here. It gets
        cleared in _on_drag_data_received after the insertion is done,
        or in _on_drag_end when the drag operation fully completes.
        """
        widget.queue_draw()
        if self._window.autohide:
            self._window.autohide.on_mouse_leave()

    def _on_drag_end(self, widget: Gtk.DrawingArea, _context: Gdk.DragContext) -> None:
        """Clean up drag state. Remove item if dragged outside dock."""
        if self._drag_from >= 0:
            # Get absolute cursor position and dock window position
            display = self._window.get_display()
            seat = display.get_default_seat()
            pointer = seat.get_pointer()
            _, screen_x, screen_y = pointer.get_position()
            win_x, win_y = self._window.get_position()
            win_w, win_h = self._window.get_size()

            # Outside if cursor moved away from the dock edge
            items = self._model.visible_items()
            pos = self._config.pos
            icon_sz = self._config.icon_size
            if pos == Position.BOTTOM:
                outside = screen_y < win_y - icon_sz
            elif pos == Position.TOP:
                outside = screen_y > win_y + win_h + icon_sz
            elif pos == Position.LEFT:
                outside = screen_x > win_x + win_w + icon_sz
            else:  # RIGHT
                outside = screen_x < win_x - icon_sz

            log.debug(
                "drag-end: screen=(%d,%d) win=(%d,%d %dx%d) outside=%s",
                screen_x,
                screen_y,
                win_x,
                win_y,
                win_w,
                win_h,
                outside,
            )

            if outside:
                if self.drag_index >= 0 and self.drag_index < len(items):
                    item = items[self.drag_index]
                    if item.is_pinned:
                        log.debug(
                            "drag-end: unpinning %s (running=%s)",
                            item.name,
                            item.is_running,
                        )
                        show_poof(int(screen_x), int(screen_y))
                        # Clear slide state to avoid stale offsets
                        self._renderer.slide_offsets.clear()
                        self._renderer.prev_positions.clear()
                        self._model.unpin_item(item.desktop_id)

        self.drag_index = -1
        self.drop_insert_index = -1
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
