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
   Drop a `.desktop` URI, executable, AppImage, file, or folder onto the dock
   to pin the matching launcher or target.

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
      +--> resolve launcher/file metadata
      +--> create generated desktop entries for executable drops when needed
      +--> pin target at insert index
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

import gi

from docking.core.items import APP_KIND, APPLET_KIND, FILE_KIND, FOLDER_KIND, DockItem
from docking.core.position import Position, is_horizontal
from docking.log import get_logger
from docking.platform.applications import entries as desktop_entries
from docking.ui.display import get_pointer_position, window_screen_position
from docking.ui.geometry import DockGeometryBuilder
from docking.ui.poof import show_poof

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.theme import Theme
    from docking.platform.applications.launcher import ApplicationLauncher
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.applications.types import ApplicationInfo
    from docking.platform.icons import IconLoader
    from docking.platform.model import DockModel
    from docking.platform.targets import TargetService
    from docking.ui.dock_window import DockWindow
    from docking.ui.folder.stack import FolderStackController
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
        geometry_builder: DockGeometryBuilder,
        folder_stack: FolderStackController,
        application_registry: ApplicationRegistry,
        application_launcher: ApplicationLauncher,
        icon_loader: IconLoader,
        target_service: TargetService,
    ) -> None:
        self._drawing_area = drawing_area
        self._window = window
        self._model = model
        self._config = config
        self._renderer = renderer
        self._theme = theme
        self._application_registry = application_registry
        self._application_launcher = application_launcher
        self._icon_loader = icon_loader
        self._target_service = target_service
        self._geometry_builder = geometry_builder
        self._folder_stack = folder_stack

        self.drag_index: int = -1
        self._drag_from: int = -1
        self.drop_insert_index: int = -1  # for external drops: where to insert
        self.drop_target_id: str = (
            ""  # launcher desktop_id under cursor during external drag
        )
        self._drop_committed: bool = False  # True after drag-drop fires
        # True only when GTK reports drag-leave for an internal dock item drag.
        # This is an input-event fact, separate from screen-coordinate math.
        # Drag-off removal uses it as the "the drag actually left the dock"
        # condition before trusting final pointer distance.
        self._internal_drag_left_dock: bool = False

        self._setup_dnd()

    def set_theme(self, theme: Theme) -> None:
        """Update the theme used for drag-and-drop spacing."""
        self._theme = theme

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
            Gtk.DestDefaults(0),
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
        # Each internal reorder starts inside the dock. The drag-off removal
        # path must earn this flag through drag-leave during this drag cycle.
        self._internal_drag_left_dock = False
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

            # Green glow on any compatible target under cursor during external drag
            gap_frame = self._geometry_builder.build_frame(
                main_cursor=-1.0, drop_insert_index=self.drop_insert_index
            )
            item = self._drop_target_item_at_point(
                gap_frame,
                x=float(x),
                y=float(y),
                accepted_kinds=(APP_KIND, APPLET_KIND),
            )
            if item is not None and item.kind == APPLET_KIND:
                applet = self._model.get_applet(item.desktop_id)
                if applet is None or not applet.accepts_drop_uris():
                    item = None
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

        # Track cursor during drag so the renderer can pin the dragged item's
        # running-indicator dot under the drag ghost instead of letting it drift
        # to the layout slot center as the model reorders.
        self._window.cursor_x = float(x)
        self._window.cursor_y = float(y)

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

        # Check if dropped onto an applet that handles file/URI drops.
        applet_drop = self._try_drop_on_applet(x=x, y=y, uris=uris)
        if applet_drop is not None:
            self.drop_insert_index = -1
            self.drop_target_id = ""
            self._reconcile_autohide_after_drag(reason="drag-data-received")
            Gtk.drag_finish(context, applet_drop, False, time)
            return

        # Check if dropped onto a launcher icon -- open files with that app.
        # A targeted launch failure is still a completed routing decision: never
        # reinterpret those files as a request to pin them to the dock.
        launcher_drop = (
            self._try_open_with_launcher(x=x, y=y, uris=uris) if uris else None
        )
        if launcher_drop is not None:
            self.drop_insert_index = -1
            self.drop_target_id = ""
            self._drop_committed = False
            self._reconcile_autohide_after_drag(reason="drag-data-received")
            Gtk.drag_finish(context, launcher_drop, False, time)
            return

        added = False
        for uri in uris:
            if self._insert_pinned_uri(uri=uri, index=insert_at):
                # External insertion should snap to the new final layout.
                # Slide offsets are useful for internal reorder, but for a
                # completed drop they make the dock look like it is slowly
                # creating room for the new item after the user already
                # committed the drop.
                self._renderer.slide_offsets.clear()
                self._renderer.prev_positions.clear()
                insert_at += 1
                added = True

        self.drop_insert_index = -1
        self.drop_target_id = ""
        self._drop_committed = False
        self._reconcile_autohide_after_drag(reason="drag-data-received")
        Gtk.drag_finish(context, added, False, time)

    def _try_drop_on_applet(self, *, x: int, y: int, uris: list[str]) -> bool | None:
        """Return drop success for applet targets, or None when not on an applet."""
        if not uris:
            return None
        frame = self._geometry_builder.build_frame(
            main_cursor=-1.0, drop_insert_index=self.drop_insert_index
        )
        item = self._drop_target_item_at_point(
            frame,
            x=float(x),
            y=float(y),
            accepted_kinds=(APPLET_KIND,),
        )
        if item is None:
            return None

        applet = self._model.get_applet(item.desktop_id)
        if applet is None or not applet.accepts_drop_uris():
            return False

        try:
            return applet.on_drop_uris(uris)
        except Exception as exc:
            log.warning("Applet drop handler failed for %s: %s", item.desktop_id, exc)
            return False

    def _try_open_with_launcher(
        self, *, x: int, y: int, uris: list[str]
    ) -> bool | None:
        """Return launch result on an app icon, or ``None`` when not targeted."""
        frame = self._geometry_builder.build_frame(
            main_cursor=-1.0, drop_insert_index=self.drop_insert_index
        )
        item = self._drop_target_item_at_point(
            frame,
            x=float(x),
            y=float(y),
            accepted_kinds=(APP_KIND,),
        )
        if item is None:
            return None

        launchable = [u for u in uris if not u.endswith(".desktop")]
        if not launchable:
            return None

        return self._application_launcher.launch_app_uris(
            item.desktop_id,
            launchable,
        )

    def _drop_target_item_at_point(
        self,
        frame,
        *,
        x: float,
        y: float,
        accepted_kinds: tuple[str, ...],
    ) -> DockItem | None:
        """Return the target icon directly under the pointer during an external drop.

        External drops should only target the visible icon itself.
        Using the broader item hit rect would make the shelf/background segment
        under an item steal drops that should land in the insertion gap.
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
            if item_geometry.item.kind not in accepted_kinds:
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

        External drops use drop_insert_index for the visual insertion gap.
        GTK can emit drag-leave before drag-drop, so external drag-leave only
        schedules a deferred gap clear. The later drop/data/end handler still
        gets a chance to consume the insert position first.

        Internal drags use drag-leave differently. Reordering is a same-widget
        operation, while dragging an item away from the dock is a destructive
        "remove from dock" gesture. A drag-leave signal is therefore recorded
        as one required condition for drag-off removal. It is not sufficient on
        its own; _on_drag_end also requires the drop not to have committed and
        the final distance check to say the pointer ended outside.
        """
        if self._drag_from < 0 and self.drop_insert_index >= 0:
            GLib.timeout_add(
                DROP_GAP_CLEAR_DELAY_MS,
                self._deferred_clear_drop_gap,
                widget,
            )
        elif self._drag_from >= 0:
            self._internal_drag_left_dock = True
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
        """Clean up drag state and optionally remove a dragged-off dock item.

        Internal reorder is finalized live during drag-motion. Drag-end is
        responsible for cleanup and for the separate drag-off removal gesture.

        Removal is intentionally gated by four current-state conditions:

        1. this drag started as an internal dock item drag (_drag_from >= 0),
        2. GTK reported that the internal drag left the dock destination,
        3. no valid same-widget drop was committed,
        4. the final pointer distance check says the pointer ended outside.

        The distance check is kept as the geometric threshold for the existing
        drag-off gesture, but it is no longer the only signal. Native Wayland
        sessions do not provide X11-style global pointer/window coordinates in
        a way this code can always trust, so the coordinate result is treated as
        evidence only after the drag event sequence also says the drag left the
        dock.
        """
        if self._drag_from >= 0:
            # Get absolute cursor position and dock window position
            display = self._window.get_display()
            pos = get_pointer_position(display)
            screen_x = pos.x if pos is not None else 0
            screen_y = pos.y if pos is not None else 0
            window_pos = window_screen_position(self._window)
            win_x, win_y = window_pos.x, window_pos.y
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

            # Conditions 2 and 3 from the docstring. If either fails, this was
            # a normal same-widget reorder/cleanup path, even when Wayland's
            # final global coordinate query appears to be outside the dock.
            if not self._internal_drag_left_dock or self._drop_committed:
                outside = False

            if outside and 0 <= self.drag_index < len(items):
                item = items[self.drag_index]
                if item.is_pinned:
                    log.debug(
                        "drag-end: unpinning %s (running=%s)",
                        item.name,
                        item.is_running,
                    )
                    if self._folder_stack.open_item_id() == item.desktop_id:
                        self._folder_stack.close()
                    show_poof(x=int(screen_x), y=int(screen_y))
                    # Clear slide state to avoid stale offsets
                    self._renderer.slide_offsets.clear()
                    self._renderer.prev_positions.clear()
                    self._model.unpin_item(item.desktop_id)

        self.drag_index = -1
        self.drop_insert_index = -1
        self.drop_target_id = ""
        self._drop_committed = False
        self._internal_drag_left_dock = False
        self._drag_from = -1
        self._config.save()
        self._reconcile_autohide_after_drag(reason="drag-end")
        widget.queue_draw()

    def _insert_pinned_uri(self, *, uri: str, index: int) -> bool:
        """Resolve and insert one external URI through the owning model boundary."""
        icon_size = self._config.scaled_icon_size
        application = self._resolve_desktop_application(uri)
        desktop_path = desktop_entries.local_path_from_uri_or_path(uri)
        is_desktop_target = (
            desktop_path is not None
            and desktop_path.suffix == desktop_entries.DESKTOP_SUFFIX
        )
        log.debug(
            "_insert_pinned_uri: uri=%s desktop_id=%s",
            uri,
            application.desktop_id if application is not None else None,
        )
        if application is not None:
            log.debug(
                "_insert_pinned_uri: resolved application desktop_id=%s",
                application.desktop_id,
            )
            return self._model.insert_pinned_application(
                desktop_id=application.desktop_id,
                index=index,
            )
        if is_desktop_target:
            desktop_id = desktop_entries.desktop_id_from_uri_or_path(uri)
            if desktop_id is not None:
                log.debug(
                    "_insert_pinned_uri: resolve returned None for %s",
                    desktop_id,
                )
                return False

        if not self._prepare_appimage_for_generation(uri):
            return False

        generated = desktop_entries.create_desktop_entry_for_executable(uri)
        if generated is not None:
            self._application_registry.refresh()
            application = self._application_registry.get(generated.desktop_id)
            if application is None:
                log.debug(
                    "_insert_pinned_uri: generated desktop entry did not resolve: %s",
                    generated.desktop_id,
                )
                return False
            log.debug(
                "_insert_pinned_uri: generated application desktop_id=%s",
                application.desktop_id,
            )
            return self._model.insert_pinned_application(
                desktop_id=application.desktop_id,
                index=index,
            )

        info = self._target_service.resolve_file(target=uri, size=icon_size)
        if info is None:
            return False
        item = DockItem(
            desktop_id=info.target,
            kind=FOLDER_KIND if info.is_dir else FILE_KIND,
            target=info.target,
            name=info.name,
            icon_name=info.icon_name,
            is_pinned=True,
            icon=info.icon,
            prefs_key=info.target,
        )
        return self._model.insert_pinned_item(item=item, index=index)

    def _resolve_desktop_application(self, target: str) -> ApplicationInfo | None:
        """Resolve desktop drops without flattening nested desktop IDs."""
        exact = self._application_registry.get(target)
        if exact is not None:
            return exact

        path = desktop_entries.local_path_from_uri_or_path(target)
        if path is None or path.suffix != desktop_entries.DESKTOP_SUFFIX:
            return None

        application = self._application_registry.resolve_by_desktop_file(path)
        if application is not None:
            return application

        desktop_id = desktop_entries.desktop_id_from_uri_or_path(target)
        if desktop_id is None:
            return None
        return self._application_registry.get(desktop_id)

    def _prepare_appimage_for_generation(self, uri: str) -> bool:
        appimage = desktop_entries.appimage_path_needing_executable_permission(uri)
        if appimage is None:
            return True
        if not self._confirm_make_appimage_executable(appimage):
            return False
        return desktop_entries.make_user_executable(appimage)

    def _confirm_make_appimage_executable(self, path: Path) -> bool:
        dialog = Gtk.MessageDialog(
            transient_for=self._window,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.CANCEL,
            text="Make AppImage executable and pin it?",
        )
        dialog.format_secondary_text(
            f"{path.name} is an AppImage, but it is not executable yet. "
            "Docking can mark it executable so it can be pinned and launched."
        )
        dialog.add_button("Make Executable and Pin", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        try:
            response = dialog.run()
        finally:
            dialog.destroy()
        return response == Gtk.ResponseType.OK

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
