"""Drag-and-drop controller for internal reorder and external application drops.

Why drag-and-drop needs its own controller

GTK drag-and-drop is not just "pointer motion with extra data". During a drag,
normal pointer event assumptions break:

- regular hover enter/leave flow is not authoritative,
- the dock can receive drag-motion without ordinary motion,
- drag-leave can occur before drop,
- external drags and internal drags have different semantics.

If the dock treated drag-over like normal pointer movement, autohide and hover
would become inconsistent very quickly. This module exists to keep drag state,
visual insertion state, and autohide policy coherent during those operations.

Two drag scenarios handled here

This module intentionally keeps both major drag paths together:

1. Internal reorder
   Move an existing dock item to a new index.

2. External drop
   Drop a `.desktop` URI onto the dock to pin a launcher.

Those are not split into separate classes because they share:

- GTK DnD registration,
- cursor-to-insertion logic,
- drag state cleanup,
- autohide suppression while dragging,
- redraw/insertion-gap behavior.

The difference is semantic:

    internal drag
      payload: dock-item-index
      action:  MOVE

    external drag
      payload: text/uri-list
      action:  COPY

Shared geometry model

DnD decisions use the same geometry frame as hover, menus, and rendering.
That matters for two reasons:

1. Reorder thresholds should line up with what the user sees.
2. External insertion gaps should line up with icon centers and dock spacing.

The important question during drag is:

    "Given the current pointer, where would an item land?"

That answer comes from shared geometry, not from DnD-specific layout code.

Internal reorder flow

The basic reorder sequence is:

    drag-begin
      |
      +--> determine dragged item from geometry
      +--> store _drag_from / drag_index
      +--> set scaled drag icon
      +--> keep dock open during drag

    drag-motion
      |
      +--> compute current insertion point
      +--> if item crossed a new boundary, reorder model
      +--> update drag_index for drawing

    drag-end
      |
      +--> clear drag visuals/state
      +--> reconcile autohide based on whether pointer is still on dock

External drop flow

External drops use a different visual model:

    drag-motion
      |
      +--> compute drop_insert_index
      +--> show insertion gap

    drag-drop / drag-data-received
      |
      +--> parse URI list
      +--> resolve launcher metadata
      +--> pin launcher at insert index
      +--> clear insertion gap
      +--> keep dock open if pointer is still on dock

One subtle but important rule:

    drag-leave does not automatically mean "hide now"

GTK can emit drag-leave before drag-drop. If the dock hid immediately there,
dropping onto the dock would feel broken. So this module defers reconciliation
until it knows whether the pointer truly left or a drop is about to complete.

Mutually exclusive drag modes

The state model is intentionally simple:

- internal drag state:
  `_drag_from`, `drag_index`

- external drag state:
  `drop_insert_index`

Only one mode should be active at a time.

ASCII sketch:

    internal reorder:
    [ A ][ B ][ C ][ D ]
             ^
             drag C left/right through item centers

    external drop:
    [ A ][ B ] |gap| [ C ][ D ]
                ^
                new launcher will be inserted here

That separation keeps rendering and drop finalization deterministic.

Autohide during drag

Dragging is a temporary "keep dock alive" interaction, even if normal hover
signals are unreliable during the operation. So this module explicitly toggles
autohide disable/hover state around drag begin/motion/leave/end/data-received
instead of assuming the ordinary event path will do it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

import gi

import docking.platform.launcher as launcher_mod
from docking.core.config import PinnedEntry
from docking.core.items import APP_KIND, FILE_KIND, FOLDER_KIND, DockItem
from docking.core.position import Position, is_horizontal
from docking.log import get_logger
from docking.ui.display import get_pointer_position
from docking.ui.geometry import DockGeometryBuilder
from docking.ui.poof import show_poof

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.theme import Theme
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel
    from docking.ui.dock_window import DockWindow
    from docking.ui.renderer import DockRenderer

log = get_logger(name="dnd")
DROP_GAP_CLEAR_DELAY_MS = 100

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
        drawing_area: Gtk.DrawingArea,
        window: DockWindow,
        model: DockModel,
        config: Config,
        renderer: DockRenderer,
        theme: Theme,
        launcher: Launcher,
        geometry_builder: DockGeometryBuilder,
    ) -> None:
        self._drawing_area = drawing_area
        self._window = window
        self._model = model
        self._config = config
        self._renderer = renderer
        self._theme = theme
        self._launcher = launcher
        self._geometry_builder = geometry_builder

        self.drag_index: int = -1
        self._drag_from: int = -1
        self.drop_insert_index: int = -1  # for external drops: where to insert
        self.drop_target_id: str = (
            ""  # launcher desktop_id under cursor during external drag
        )
        self._drop_committed: bool = False  # True after drag-drop fires

        self._setup_dnd()

    def _setup_dnd(self) -> None:
        """Configure GTK drag-and-drop on the drawing area.

        Source: left-button drag of dock-item-index (internal reorder).
        Dest: no DestDefaults (manual motion/drop handling) accepting
        both dock-item-index and text/uri-list for external .desktop drops.
        Skips source/dest setup if icons are locked.
        """
        da = self._drawing_area

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
        da = da or self._drawing_area
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
        da = da or self._drawing_area
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

        Hit-tests the current cursor against the shared geometry frame to find
        the dragged item, stores its index in drag_index/_drag_from, and sets
        a scaled pixbuf as the drag icon.
        """
        frame = self._geometry_builder.build_frame()
        self._begin_drag_autohide()
        items = self._model.visible_items()
        horizontal = is_horizontal(pos=self._config.pos)
        cursor_x, cursor_y = self._window.cursor_x, self._window.cursor_y
        win_cx = cursor_x if horizontal else cursor_y
        dragged_index = frame.item_index_at_point(cursor_x, cursor_y)
        log.debug(
            "drag-begin: win_cx=%.1f items=%d",
            win_cx,
            len(items),
        )
        for i, item_geometry in enumerate(frame.item_geometries):
            left = (
                item_geometry.draw_rect.x if horizontal else item_geometry.draw_rect.y
            )
            right = left + (
                item_geometry.draw_rect.w if horizontal else item_geometry.draw_rect.h
            )
            log.debug(
                "  item %d: left=%.1f right=%.1f (win_cx=%.1f)", i, left, right, win_cx
            )
            if i == dragged_index:
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
                log.debug(f"  -> dragging item {i}: {item.name}")
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
        self._on_drag_motion_enter()
        main_coord = x if is_horizontal(pos=self._config.pos) else y

        if self._drag_from < 0:
            # External drag -- compute insert position for gap effect
            frame = self._geometry_builder.build_frame(main_cursor=-1.0)
            insert = frame.insertion_index_for_main(main_coord, pos=self._config.pos)
            changed = insert != self.drop_insert_index
            if changed:
                self.drop_insert_index = insert

            # Green glow on any app icon under cursor during external drag
            gap_frame = self._geometry_builder.build_frame(
                main_cursor=-1.0, drop_insert_index=self.drop_insert_index
            )
            item = self._drop_target_item_at_point(gap_frame, x=float(x), y=float(y))
            new_target = item.desktop_id if item is not None else ""
            if new_target != self.drop_target_id:
                self.drop_target_id = new_target
                changed = True

            if changed:
                widget.queue_draw()
            Gdk.drag_status(context, Gdk.DragAction.COPY, time)
            return True

        frame = self._geometry_builder.build_frame(main_cursor=-1.0)
        new_index = frame.insertion_index_for_main(main_coord, pos=self._config.pos)
        if frame.item_geometries:
            new_index = min(new_index, len(frame.item_geometries) - 1)
        else:
            new_index = -1

        if new_index != self.drag_index:
            log.debug(f"drag-motion: reorder {self.drag_index} -> {new_index}")
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
        self._drop_committed = True
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
        x: int,
        y: int,
        selection: Gtk.SelectionData,
        _info: int,
        time: int,
    ) -> None:
        """Process drop data -- noop for internal reorder, open or pin for external.

        Internal reorder is already handled live in drag-motion; this just
        acknowledges completion. External drops either open files with the
        target app (if dropped onto an app icon) or insert pinned items.
        """
        # Internal reorder -- already handled during drag-motion
        if self._drag_from >= 0:
            log.debug("drag-data-received: internal reorder complete")
            self._reconcile_autohide_after_drag(reason="drag-data-received")
            Gtk.drag_finish(context, True, False, time)
            return

        # External drop -- process URIs
        insert_at = max(0, self.drop_insert_index)
        log.debug(f"drag-data-received: external drop, insert_at={insert_at}")
        uris = selection.get_uris()
        if not uris:
            text = selection.get_text()
            if text:
                uris = [line.strip() for line in text.splitlines() if line.strip()]

        # Check if dropped onto a launcher icon -- open files with that app
        if uris and self._try_open_with_launcher(x=x, y=y, uris=uris):
            self.drop_insert_index = -1
            self._reconcile_autohide_after_drag(reason="drag-data-received")
            Gtk.drag_finish(context, True, False, time)
            return

        added = False
        for uri in uris:
            item = self._item_from_uri(uri=uri)
            if item and not self._model.find_by_desktop_id(item.desktop_id):
                insert_at = min(insert_at, len(self._model.pinned_items))
                self._model.pinned_items.insert(insert_at, item)
                self._config.pinned.insert(
                    insert_at,
                    PinnedEntry(kind=item.kind, target=item.target),
                )
                self._model.sync_pinned_to_config()
                self._config.save()
                # External insertion should snap to the new final layout.
                # Slide offsets are useful for internal reorder, but for a
                # completed drop they make the dock look like it is slowly
                # creating room for the new item after the user already
                # committed the drop.
                self._renderer.slide_offsets.clear()
                self._renderer.prev_positions.clear()
                self._model.notify()
                insert_at += 1
                added = True

        self.drop_insert_index = -1
        self.drop_target_id = ""
        self._drop_committed = False
        self._reconcile_autohide_after_drag(reason="drag-data-received")
        Gtk.drag_finish(context, added, False, time)

    def _try_open_with_launcher(self, *, x: int, y: int, uris: list[str]) -> bool:
        """If drop lands on an app icon, try opening the files with it."""
        frame = self._geometry_builder.build_frame(
            main_cursor=-1.0, drop_insert_index=self.drop_insert_index
        )
        item = self._drop_target_item_at_point(frame, x=float(x), y=float(y))
        if item is None:
            return False

        launchable = [u for u in uris if not u.endswith(".desktop")]
        if not launchable:
            return False

        try:
            app_info = Gio.DesktopAppInfo.new(item.desktop_id)
        except (TypeError, GLib.Error) as exc:
            log.debug(
                "Failed to resolve desktop app info for drop target %s: %s",
                item.desktop_id,
                exc,
            )
            return False
        if not app_info:
            return False

        try:
            app_info.launch_uris(launchable, None)
            log.debug("Opened %d file(s) with %s", len(launchable), item.desktop_id)
            return True
        except GLib.Error as exc:
            log.warning("Failed to open with %s: %s", item.desktop_id, exc)
            return False

    def _drop_target_item_at_point(
        self, frame, *, x: float, y: float
    ) -> DockItem | None:
        """Return the app icon directly under the pointer during an external drop.

        External launcher drops should only target the visible app icon itself.
        Using the broader item hit rect would make the shelf/background segment
        under an app steal drops that should land in the insertion gap.
        """
        if not frame.cursor_rect.contains(x=x, y=y):
            return None
        gap = (
            self._config.icon_size + self._theme.item_padding
            if self.drop_insert_index >= 0
            else 0
        )
        horizontal = is_horizontal(pos=self._config.pos)
        for index, item_geometry in enumerate(frame.item_geometries):
            if item_geometry.item.kind != APP_KIND:
                continue
            draw_rect = item_geometry.draw_rect
            if gap > 0 and index >= self.drop_insert_index:
                if horizontal:
                    left = draw_rect.x + gap
                    top = draw_rect.y
                else:
                    left = draw_rect.x
                    top = draw_rect.y + gap
                contains = (
                    left <= x < left + draw_rect.w and top <= y < top + draw_rect.h
                )
            else:
                contains = draw_rect.contains(x=x, y=y)
            if contains:
                return item_geometry.item
        return None

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
            GLib.timeout_add(
                DROP_GAP_CLEAR_DELAY_MS,
                self._deferred_clear_drop_gap,
                widget,
            )
        self.drop_target_id = ""
        widget.queue_draw()

    def _deferred_clear_drop_gap(self, widget: Gtk.DrawingArea) -> bool:
        """Clear stale drop gap if it wasn't consumed by a drop."""
        if self.drop_insert_index >= 0 and self._drag_from < 0:
            self.drop_insert_index = -1
            self._reconcile_autohide_after_drag(reason="drag-leave")
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
            pos = get_pointer_position(display)
            screen_x = pos.x if pos is not None else 0
            screen_y = pos.y if pos is not None else 0
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

            if outside and 0 <= self.drag_index < len(items):
                item = items[self.drag_index]
                if item.is_pinned:
                    log.debug(
                        "drag-end: unpinning %s (running=%s)",
                        item.name,
                        item.is_running,
                    )
                    self._window.close_open_folder_stack_for_item(item.desktop_id)
                    show_poof(x=int(screen_x), y=int(screen_y))
                    # Clear slide state to avoid stale offsets
                    self._renderer.slide_offsets.clear()
                    self._renderer.prev_positions.clear()
                    self._model.unpin_item(item.desktop_id)

        self.drag_index = -1
        self.drop_insert_index = -1
        self.drop_target_id = ""
        self._drop_committed = False
        self._drag_from = -1
        self._config.save()
        self._reconcile_autohide_after_drag(reason="drag-end")
        widget.queue_draw()

    def _item_from_uri(self, uri: str) -> DockItem | None:
        """Build a pinned DockItem from an external URI drop."""
        desktop_id = self._uri_to_desktop_id(uri)
        icon_size = int(self._config.icon_size * self._config.zoom_percent)
        if desktop_id:
            resolved = self._launcher.resolve(desktop_id)
            if resolved is None:
                return None
            icon = self._launcher.load_icon(resolved.icon_name, icon_size)
            return DockItem(
                desktop_id=desktop_id,
                kind=APP_KIND,
                target=desktop_id,
                name=resolved.name,
                icon_name=resolved.icon_name,
                wm_class=resolved.wm_class,
                is_pinned=True,
                icon=icon,
            )

        info = self._launcher.resolve_file(target=uri, size=icon_size)
        if info is None:
            return None
        return DockItem(
            desktop_id=info.target,
            kind=FOLDER_KIND if info.is_dir else FILE_KIND,
            target=info.target,
            name=info.name,
            icon_name=info.icon_name,
            is_pinned=True,
            icon=info.icon,
            prefs_key=info.target,
        )

    @staticmethod
    def _uri_to_desktop_id(uri: str) -> str | None:
        """Extract a .desktop ID from a file URI or path."""
        normalized = launcher_mod.normalize_file_target(uri)
        if normalized is None:
            return None
        path = Path(unquote(urlparse(normalized).path))
        if not path.name.endswith(".desktop"):
            return None
        return path.name

    def _begin_drag_autohide(self) -> None:
        if self._window.autohide.enabled:
            self._window.autohide.set_disabled(True, reason="drag-begin")

    def _on_drag_motion_enter(self) -> None:
        if self._window.autohide.enabled:
            self._window.autohide.set_disabled(True, reason="drag-motion")
            self._window.autohide.on_mouse_enter()

    def _reconcile_autohide_after_drag(self, *, reason: str) -> None:
        if not self._window.autohide.enabled:
            return
        if self._window.is_pointer_inside_dock():
            self._window.autohide.set_hovered(True)
            self._window.autohide.set_disabled(False, reason=f"{reason}-inside")
            return
        self._window.autohide.set_hovered(False)
        self._window.autohide.set_disabled(False, reason=f"{reason}-outside")
        self._window.autohide.on_mouse_leave()
