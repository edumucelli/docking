"""Hover coordination for items, tooltips, previews, and short animation pumps.

Why hover needs its own module

"Hovered item" sounds simple until you list everything that depends on it:

- icon zoom/displacement,
- tooltip text and position,
- preview show delay,
- preview hide policy,
- redraw pumping for short-lived visual effects,
- the distinction between "pointer is on the dock" and "pointer is on this item".

If each of those features ran its own local hover state, they would drift.
The classic failure modes are:

- tooltip for item A while preview is opening for item B,
- preview timer firing after the pointer already moved away,
- repeated micro-timers trying to pump redraws independently,
- hover clearing too early while autohide is still smoothing the dock out.

This module centralizes that state.

What this module owns

HoverManager owns:

- the currently hovered item,
- preview show timer lifecycle,
- cancellation of stale preview requests,
- tooltip update decisions tied to hovered item changes,
- a shared short-lived redraw pump for click/urgent animations.

It does not own:

- dock-wide hover (`dock_hovered` belongs to interaction policy),
- geometry building (it consumes a frame),
- autohide state,
- preview rendering,
- tooltip rendering.

The distinction between dock hover and item hover

These are different concepts:

- dock hover
  The pointer is inside the dock's active cursor region.

- item hover
  The pointer is inside one item's hover_rect.

ASCII view:

    +-----------------------------------------------+
    |                cursor_rect                    |
    |   +-----+  +-----+  +-----+  +-----+          |
    |   |item |  |item |  |item |  |item |          |
    |   +-----+  +-----+  +-----+  +-----+          |
    |                                               |
    |    background zone: on dock, but no item      |
    +-----------------------------------------------+

The manager only decides item hover. Whether the dock is effectively hovered is
decided elsewhere by interaction policy.

Normal hover flow

The usual path is:

    motion event
      |
      +--> current geometry frame
      |
      +--> hit hover_item_at_point(...)
      |
      +--> update tooltip immediately
      |
      +--> if item changed:
              cancel pending preview timer
              set new hovered_item
              maybe arm preview timer

Timeline:

    pointer enters item
       |
       +-- tooltip updates immediately
       |
       +-- preview waits PREVIEW_SHOW_DELAY_MS
       |
       +-- if pointer is still on that item, preview shows

That delay matters because previews are heavier and more disruptive than a
simple tooltip. The user should be able to glide across icons without opening a
full preview popup for each one.

Why preview show recomputes from fresh geometry

The preview timer stores intent, not stale positions.

When the delayed callback fires, the dock may have changed due to:

- cursor movement,
- zoom animation,
- autohide slide position,
- item reorder,
- model changes,
- monitor/display changes.

So the preview must use fresh geometry at timer-fire time, not the geometry
that existed when the timer was armed.

Tooltip policy in this module

Tooltip updates are intentionally immediate when allowed because they are the
lightweight feedback channel. But they are not allowed in every dock state.

One important policy encoded here:

    SHOWING state => suppress tooltip display

Why:
- during SHOWING the dock is still sliding in,
- tooltip anchors would be valid but transient,
- the tooltip would appear too close to the screen edge and visibly chase the
  dock upward/downward.

So hover state still updates, but tooltip visibility waits for stable geometry.

Animation pump ownership

Some visual effects need a bounded redraw loop:

- click darken,
- launch bounce,
- urgent bounce/glow.

This module owns one shared short-lived redraw pump instead of letting every
effect create its own timer. That keeps redraw activity bounded and easier to
reason about:

    effect starts
      |
      +--> HoverManager starts anim pump
      |
      +--> queue_draw at ~60fps for duration
      |
      +--> timer stops itself

This is not "hover logic" in the narrow sense, but it belongs here because
these effects are driven by the same user-attention transitions around items.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib  # noqa: E402

from docking.core.position import Position
from docking.log import get_logger
from docking.ui.autohide import HideState
from docking.ui.geometry import DockGeometryBuilder, DockGeometryFrame

_log = get_logger(name="hover")

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem
    from docking.core.theme import Theme
    from docking.platform.model import DockModel
    from docking.ui.dock_window import DockWindow
    from docking.ui.preview import PreviewPopup
    from docking.ui.tooltip import TooltipManager

PREVIEW_SHOW_DELAY_MS = 400
ANIM_PUMP_DEFAULT_DURATION_MS = 700
ANIM_PUMP_FRAME_INTERVAL_MS = 16


class HoverManager:
    """Tracks which dock icon is hovered and manages preview/animation timers."""

    def __init__(
        self,
        window: DockWindow,
        config: Config,
        model: DockModel,
        theme: Theme,
        tooltip: TooltipManager,
        geometry_builder: DockGeometryBuilder,
    ) -> None:
        """Initialize with references to dock subsystems.

        Depends on window (for hit_test and coordinate conversion),
        config/theme (for layout computation), model (for item list),
        and tooltip (to show/hide on hover changes). Preview is set
        later via set_preview() since it's constructed after the window.
        """
        self._window = window
        self._config = config
        self._model = model
        self._theme = theme
        self._tooltip = tooltip
        self._geometry_builder = geometry_builder
        self._preview: PreviewPopup | None = None

        self.hovered_item: DockItem | None = None
        self._preview_timer_id: int = 0
        self._anim_timer_id: int = 0

    def set_preview(self, preview: PreviewPopup) -> None:
        self._preview = preview

    def update(
        self, cursor_main: float, frame: DockGeometryFrame | None = None
    ) -> None:
        """Detect which item the cursor is over and manage preview timer."""
        frame = frame or self._geometry_builder.build_frame()
        if not self._window.dock_hovered:
            self._tooltip.hide()
            return
        item = (
            frame.hover_item_at_point(self._window.cursor_x, self._window.cursor_y)
            if cursor_main >= 0
            else None
        )

        # Tooltip anchoring should use stable visible geometry. During SHOWING
        # the dock is still sliding in, so tooltips would appear too close to
        # the screen edge and then visibly chase the moving dock. Keep hover
        # state updating, but suppress tooltip display until the dock reaches
        # VISIBLE.
        autohide = self._window.autohide
        if autohide and autohide.enabled and autohide.state == HideState.SHOWING:
            self._tooltip.hide()
        else:
            # Always refresh tooltip when allowed (item.name may change while
            # hovered)
            self._tooltip.update(item, frame)

        if item is self.hovered_item:
            return

        previous_item = self.hovered_item
        if previous_item is not None and item is None:
            previous_geometry = frame.geometry_for_item(previous_item)
            _log.debug(
                (
                    "hover exit: item=%s cursor=(%.0f,%.0f) "
                    "cursor_rect=%s hover_rect=%s draw_rect=%s"
                ),
                previous_item.desktop_id,
                self._window.cursor_x,
                self._window.cursor_y,
                frame.cursor_rect,
                previous_geometry.hover_rect if previous_geometry else None,
                previous_geometry.draw_rect if previous_geometry else None,
            )
        _log.debug(
            f"hover changed: "
            f"{self.hovered_item.name if self.hovered_item else None} -> "
            f"{item.name if item else None}"
        )
        self.hovered_item = item
        self.cancel()

        if self._preview and self._config.previews_enabled:
            if item and item.is_running and item.instance_count > 0:
                self._preview_timer_id = GLib.timeout_add(
                    PREVIEW_SHOW_DELAY_MS, self._show_preview, item, frame
                )
            else:
                self._preview.schedule_hide()

    def cancel(self) -> None:
        """Cancel any pending preview show timer."""
        if self._preview_timer_id:
            GLib.source_remove(self._preview_timer_id)
            self._preview_timer_id = 0

    def start_anim_pump(
        self,
        duration_ms: int = ANIM_PUMP_DEFAULT_DURATION_MS,
    ) -> None:
        """Start a temporary 60fps redraw loop for time-based animations.

        Used for click darken (300ms), launch bounce (600ms), and urgent
        bounce. The pump self-terminates after duration_ms.
        """
        if self._anim_timer_id:
            GLib.source_remove(self._anim_timer_id)

        frames_left = duration_ms // ANIM_PUMP_FRAME_INTERVAL_MS

        def tick() -> bool:
            nonlocal frames_left
            frames_left -= 1
            if frames_left <= 0:
                self._anim_timer_id = 0
                return False
            self._window.drawing_area.queue_draw()
            return True

        self._anim_timer_id = GLib.timeout_add(ANIM_PUMP_FRAME_INTERVAL_MS, tick)

    def on_model_changed(self) -> None:
        """Start anim pump if any item became urgent (needs bounce animation)."""
        if any(
            item.is_urgent and item.last_urgent > 0
            for item in self._model.visible_items()
        ):
            self.start_anim_pump(duration_ms=ANIM_PUMP_DEFAULT_DURATION_MS)

    def _show_preview(self, item: DockItem, _layout: object) -> bool:
        """Show the preview popup near the hovered icon.

        Called after PREVIEW_SHOW_DELAY_MS by GLib.timeout_add. The
        _layout parameter is from the timeout closure but is stale, so
        we recompute layout with the current cursor position to get
        accurate icon coordinates for anchor placement.
        """
        self._preview_timer_id = 0
        if not self._preview or self.hovered_item is not item:
            return False
        if not self._window.get_realized():
            return False

        frame = self._geometry_builder.build_frame()
        geometry = frame.geometry_for_item(item)
        if geometry is None:
            return False
        pos = self._config.pos

        win_x, win_y = self._window.get_position()
        draw_rect = geometry.draw_rect
        main_size = (
            draw_rect.w if pos in (Position.BOTTOM, Position.TOP) else draw_rect.h
        )

        if pos == Position.BOTTOM:
            icon_abs_x = win_x + draw_rect.x
            icon_top_y = win_y + draw_rect.y
            self._preview.show_for_item(
                item.desktop_id, icon_abs_x, main_size, icon_top_y, pos
            )
        elif pos == Position.TOP:
            icon_abs_x = win_x + draw_rect.x
            icon_bottom_y = win_y + draw_rect.y + draw_rect.h
            self._preview.show_for_item(
                item.desktop_id, icon_abs_x, main_size, icon_bottom_y, pos
            )
        elif pos == Position.LEFT:
            icon_abs_y = win_y + draw_rect.y
            icon_right_x = win_x + draw_rect.x + draw_rect.w
            self._preview.show_for_item(
                item.desktop_id, icon_right_x, main_size, icon_abs_y, pos
            )
        else:  # RIGHT
            icon_abs_y = win_y + draw_rect.y
            icon_left_x = win_x + draw_rect.x
            self._preview.show_for_item(
                item.desktop_id, icon_left_x, main_size, icon_abs_y, pos
            )
        return False
