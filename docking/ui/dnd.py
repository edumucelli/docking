"""Drag-and-drop: internal reordering + external .desktop file drops."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from docking.log import get_logger

log = get_logger(name="dnd")

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from docking.core.position import Position, is_horizontal
from docking.core.zoom import compute_layout
from docking.platform.model import DockItem
from docking.ui.poof import show_poof

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.theme import Theme
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel
    from docking.ui.dock_window import DockWindow
    from docking.ui.renderer import DockRenderer

DRAG_ICON_SCALE = 1.2  # dragged icon shown at this multiplier of icon_size

# DnD target formats:
# - dock-item-index: internal reorder (SAME_WIDGET only, info=0)
# - text/uri-list: external .desktop file drops from file managers (info=1)
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
        """Configure GTK drag-and-drop on the drawing area.

        Source: left-button drag of dock-item-index (internal reorder).
        Dest: no DestDefaults (manual motion/drop handling) accepting
        both dock-item-index and text/uri-list for external .desktop drops.
        Skips source/dest setup if icons are locked.
        """
        da = self._window.drawing_area

        if not self._config.lock_icons:
            self._enable_dnd(da=da)

        da.connect("drag-begin", self._on_drag_begin)
        da.connect("drag-motion", self._on_drag_motion)
        da.connect("drag-drop", self._on_drag_drop)
        da.connect("drag-end", self._on_drag_end)
        da.connect("drag-data-received", self._on_drag_data_received)
        da.connect("drag-leave", self._on_drag_leave)

    def _enable_dnd(self, da: Gtk.DrawingArea | None = None) -> None:
        """Enable drag source and dest on the drawing area."""
        da = da or self._window.drawing_area
        da.drag_source_set(
            Gdk.ModifierType.BUTTON1_MASK,
            [_DOCK_ITEM_TARGET],
            Gdk.DragAction.MOVE,
        )
        da.drag_dest_set(
            0,
            [_DOCK_ITEM_TARGET, _URI_TARGET],
            Gdk.DragAction.MOVE | Gdk.DragAction.COPY,
        )

    def _disable_dnd(self, da: Gtk.DrawingArea | None = None) -> None:
        """Disable drag source and dest on the drawing area."""
        da = da or self._window.drawing_area
        da.drag_source_unset()
        da.drag_dest_unset()

    def set_locked(self, locked: bool) -> None:
        """Toggle DnD based on lock state."""
        if locked:
            self._disable_dnd()
        else:
            self._enable_dnd()

    def _on_drag_begin(self, widget: Gtk.DrawingArea, context: Gdk.DragContext) -> None:
        """Identify which item is being dragged and set the drag icon.

        Hit-tests the current cursor against the layout to find the
        dragged item, stores its index in drag_index/_drag_from, and
        sets a scaled pixbuf as the drag icon.
        """
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
        horizontal = is_horizontal(pos=self._config.pos)
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
        """Update drop position as user drags over the dock.

        For internal drags: live-reorders items as the cursor crosses
        icon center boundaries. For external drags: tracks the insert
        position to render a gap in the icon layout.
        """
        # GTK drag-and-drop event model quirk:
        #
        # During an active drag operation (user is dragging something),
        # GTK takes over mouse event delivery. The normal widget signals
        # that fire during regular mouse movement do not fire during DnD:
        #
        #   Normal hover:    enter-notify -> motion-notify -> leave-notify
        #   During DnD:      drag-motion  -> (no enter/leave!) -> drag-leave
        #
        # This means our autohide controller's on_mouse_enter() -- which
        # is triggered by enter-notify-event -- would never fire when the
        # user drags a .desktop file toward the dock to add it.
        #
        # To fix this, we explicitly call autohide.on_mouse_enter() from
        # the drag-motion handler, which IS delivered during DnD.
        if self._window.autohide:
            self._window.autohide.on_mouse_enter()
        main_coord = x if is_horizontal(pos=self._config.pos) else y

        if self._drag_from < 0:
            # External drag -- compute insert position for gap effect
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
        """Handle the drop event -- request URI data for external drops."""
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
        # No matching target (e.g. applet URI) -- clear the gap
        self.drop_insert_index = -1
        widget.queue_draw()
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
        """Process drop data -- noop for internal reorder, pin for external URIs.

        Internal reorder is already handled live in drag-motion; this just
        acknowledges completion. External drops parse URI list, resolve
        .desktop files, and insert pinned items at the drop position.
        """
        # Internal reorder -- already handled during drag-motion
        if self._drag_from >= 0:
            log.debug("drag-data-received: internal reorder complete")
            Gtk.drag_finish(context, True, False, time)
            return

        # External drop -- process URIs
        insert_at = max(0, self.drop_insert_index)
        log.debug("drag-data-received: external drop, insert_at=%d", insert_at)
        uris = selection.get_uris()
        if not uris:
            text = selection.get_text()
            if text:
                uris = [line.strip() for line in text.splitlines() if line.strip()]

        added = False
        for uri in uris:
            desktop_id = self._uri_to_desktop_id(uri=uri)
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

        GTK fires drag-leave before drag-drop, so we can't clear
        drop_insert_index here (drag-data-received still needs it).
        Instead we schedule a deferred clear -- if a drop happens,
        drag-data-received or drag-end will clear it first. If the
        drag truly left (cancelled), the deferred clear closes the gap.
        """
        if self._drag_from < 0 and self.drop_insert_index >= 0:
            GLib.timeout_add(100, self._deferred_clear_drop_gap, widget)
        widget.queue_draw()
        if self._window.autohide:
            self._window.autohide.on_mouse_leave()

    def _deferred_clear_drop_gap(self, widget: Gtk.DrawingArea) -> bool:
        """Clear stale drop gap if it wasn't consumed by a drop."""
        if self.drop_insert_index >= 0 and self._drag_from < 0:
            self.drop_insert_index = -1
            widget.queue_draw()
        return False

    def _on_drag_end(self, widget: Gtk.DrawingArea, _context: Gdk.DragContext) -> None:
        """Clean up drag state and unpin if item was dragged outside the dock.

        Checks if the cursor ended up beyond the icon_size threshold from
        the dock edge. If so, unpins the item and plays the poof animation.
        """
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
                        show_poof(x=int(screen_x), y=int(screen_y))
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
