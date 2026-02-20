"""Drag-and-drop: internal reordering + external .desktop file drops."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse, unquote

from docking.log import get_logger

log = get_logger("dnd")

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib  # noqa: E402

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
        from docking.zoom import compute_layout
        items = self._model.visible_items()
        local_cx = self._window._local_cursor_x()
        layout = compute_layout(
            items, self._config, local_cx,
            item_padding=self._theme.item_padding,
            h_padding=self._theme.h_padding,
        )

        offset = self._window._zoomed_x_offset(layout)
        win_cx = self._window.cursor_x
        log.debug("drag-begin: win_cx=%.1f local_cx=%.1f offset=%.1f items=%d",
                   win_cx, local_cx, offset, len(items))
        for i, li in enumerate(layout):
            icon_w = li.scale * self._config.icon_size
            left = li.x + offset
            right = left + icon_w
            log.debug("  item %d: left=%.1f right=%.1f (win_cx=%.1f)", i, left, right, win_cx)
            if left <= win_cx <= right:
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
                log.debug("  -> dragging item %d: %s", i, item.name)
                return
        log.debug("  -> no item matched")

    def _on_drag_motion(
        self, widget: Gtk.DrawingArea, context: Gdk.DragContext,
        x: int, y: int, time: int,
    ) -> bool:
        """Update drop position as user drags."""
        # Reveal dock when dragging over it (enter-notify doesn't fire during DnD)
        if self._window._autohide:
            self._window._autohide.on_mouse_enter()
        if self._drag_from < 0:
            # External drag — compute insert position for gap effect
            from docking.zoom import compute_layout
            items = self._model.visible_items()
            layout = compute_layout(
                items, self._config, -1.0,
                item_padding=self._theme.item_padding,
                h_padding=self._theme.h_padding,
            )
            x_offset = self._window._zoomed_x_offset(layout)
            insert = len(layout)
            for i, li in enumerate(layout):
                center = li.x + x_offset + self._config.icon_size / 2
                if x < center:
                    insert = i
                    break
            if insert != self.drop_insert_index:
                self.drop_insert_index = insert
                widget.queue_draw()
            Gdk.drag_status(context, Gdk.DragAction.COPY, time)
            return True

        from docking.zoom import compute_layout
        items = self._model.visible_items()
        layout = compute_layout(
            items, self._config, -1.0,
            item_padding=self._theme.item_padding,
            h_padding=self._theme.h_padding,
        )

        x_offset = self._window._zoomed_x_offset(layout)
        new_index = len(layout) - 1
        for i, li in enumerate(layout):
            center = li.x + x_offset + self._config.icon_size / 2
            if x < center:
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
        self, widget: Gtk.DrawingArea, context: Gdk.DragContext,
        x: int, y: int, time: int,
    ) -> bool:
        """Request data for external drops, finalize internal drops."""
        target = widget.drag_dest_find_target(context, None)
        log.debug("drag-drop: drag_from=%d insert=%d target=%s",
                  self._drag_from, self.drop_insert_index, target)
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
                    from docking.dock_model import DockItem
                    item = DockItem(
                        desktop_id=desktop_id,
                        name=resolved.name,
                        icon_name=resolved.icon_name,
                        wm_class=resolved.wm_class,
                        is_pinned=True,
                        icon=icon,
                    )
                    # Insert at drop position
                    insert_at = min(insert_at, len(self._model._pinned))
                    self._model._pinned.insert(insert_at, item)
                    self._config.pinned.insert(insert_at, desktop_id)
                    insert_at += 1
                    added = True

        self.drop_insert_index = -1
        if added:
            self._config.save()
            self._model._sync_pinned_to_config()
            self._model._notify()

        Gtk.drag_finish(context, added, False, time)

    def _on_drag_leave(self, widget: Gtk.DrawingArea, context: Gdk.DragContext, time: int) -> None:
        """Hide dock when drag leaves (leave-notify doesn't fire during DnD).

        Note: don't clear drop_insert_index here — GTK fires drag-leave
        before drag-drop, so we need the index to survive until data-received.
        """
        widget.queue_draw()
        if self._window._autohide:
            self._window._autohide.on_mouse_leave()

    def _on_drag_end(self, widget: Gtk.DrawingArea, context: Gdk.DragContext) -> None:
        """Clean up drag state. Remove item if dragged outside dock."""
        if self._drag_from >= 0:
            # Get absolute cursor position and dock window position
            display = self._window.get_display()
            seat = display.get_default_seat()
            pointer = seat.get_pointer()
            _, screen_x, screen_y = pointer.get_position()
            win_x, win_y = self._window.get_position()
            win_w, win_h = self._window.get_size()

            # Outside if cursor Y is above the dock window or far below
            items = self._model.visible_items()
            outside = screen_y < win_y - self._config.icon_size

            log.debug("drag-end: screen=(%d,%d) win=(%d,%d %dx%d) outside=%s",
                       screen_x, screen_y, win_x, win_y, win_w, win_h, outside)

            if outside:
                if self.drag_index >= 0 and self.drag_index < len(items):
                    item = items[self.drag_index]
                    if item.is_pinned:
                        log.debug("drag-end: unpinning %s (running=%s)",
                                  item.name, item.is_running)
                        _show_poof(int(screen_x), int(screen_y))
                        # Clear slide state to avoid stale offsets
                        self._renderer._slide_offsets.clear()
                        self._renderer._prev_positions.clear()
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


# -- Poof animation --

_POOF_DURATION_MS = 300
_POOF_SIZE = 80
_POOF_FRAMES = 18


_poof_pixbuf: GdkPixbuf.Pixbuf | None = None
_poof_loaded = False


def _load_poof() -> GdkPixbuf.Pixbuf | None:
    global _poof_pixbuf, _poof_loaded
    if _poof_loaded:
        return _poof_pixbuf
    _poof_loaded = True
    svg_path = str(Path(__file__).parent / "poof.svg")
    try:
        _poof_pixbuf = GdkPixbuf.Pixbuf.new_from_file(svg_path)
    except Exception:
        log.warning("poof.svg not found at %s", svg_path)
    return _poof_pixbuf


def _show_poof(x: int, y: int) -> None:
    """Show Plank's poof sprite-sheet animation at (x, y) screen coords."""
    pixbuf = _load_poof()
    if pixbuf is None:
        return

    frame_size = pixbuf.get_width()
    num_frames = pixbuf.get_height() // frame_size
    if num_frames < 1:
        return

    win = Gtk.Window(type=Gtk.WindowType.POPUP)
    win.set_decorated(False)
    win.set_skip_taskbar_hint(True)
    win.set_app_paintable(True)
    win.set_size_request(frame_size, frame_size)

    screen = win.get_screen()
    visual = screen.get_rgba_visual()
    if visual:
        win.set_visual(visual)

    # Store animation state on the window itself to prevent GC issues
    win._poof_frame = 0
    win._poof_pixbuf = pixbuf
    win._poof_frame_size = frame_size
    win._poof_num_frames = num_frames

    def on_draw(widget, cr):
        import cairo
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        f = min(widget._poof_frame, widget._poof_num_frames - 1)
        Gdk.cairo_set_source_pixbuf(cr, widget._poof_pixbuf, 0, -widget._poof_frame_size * f)
        cr.rectangle(0, 0, widget._poof_frame_size, widget._poof_frame_size)
        cr.fill()
        return True

    def tick(w):
        w._poof_frame += 1
        log.debug("poof tick: frame=%d/%d mapped=%s", w._poof_frame, w._poof_num_frames, w.get_mapped())
        if w._poof_frame >= w._poof_num_frames:
            log.debug("poof: destroying window")
            w.destroy()
            return False
        w.queue_draw()
        return True

    win.connect("draw", on_draw)
    win.move(x - frame_size // 2, y - frame_size // 2)
    win.show_all()
    log.debug("poof: shown at (%d,%d) frames=%d interval=%dms", x, y, num_frames, _POOF_DURATION_MS // num_frames)
    GLib.timeout_add(_POOF_DURATION_MS // num_frames, tick, win)
