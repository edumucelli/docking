"""Hover manager — icon hover detection, preview popup timer, animation pump."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib  # noqa: E402

from docking.core.zoom import compute_layout

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.theme import Theme
    from docking.platform.model import DockModel, DockItem
    from docking.ui.preview import PreviewPopup
    from docking.ui.tooltip import TooltipManager
    from docking.ui.dock_window import DockWindow

PREVIEW_SHOW_DELAY_MS = 400  # hover delay before showing preview popup


class HoverManager:
    """Tracks which dock icon is hovered and manages preview/animation timers.

    Responsibilities:
    - Detect which item the cursor is over via hit testing
    - Start/cancel the preview popup delay timer
    - Run the animation pump for click/bounce effects
    - Notify the tooltip manager on hover changes
    """

    def __init__(
        self,
        window: DockWindow,
        config: Config,
        model: DockModel,
        theme: Theme,
        tooltip: TooltipManager,
    ) -> None:
        self._window = window
        self._config = config
        self._model = model
        self._theme = theme
        self._tooltip = tooltip
        self._preview: PreviewPopup | None = None

        self.hovered_item: DockItem | None = None
        self._preview_timer_id: int = 0
        self._anim_timer_id: int = 0  # redraw pump for click/bounce animations

    def set_preview(self, preview: PreviewPopup) -> None:
        """Set the preview popup reference (wired after construction)."""
        self._preview = preview

    def update(self, cursor_x: float) -> None:
        """Detect which item the cursor is over and manage preview timer."""
        items = self._model.visible_items()
        layout = compute_layout(
            items,
            self._config,
            self._window.local_cursor_x(),
            item_padding=self._theme.item_padding,
            h_padding=self._theme.h_padding,
        )
        item = self._window.hit_test(cursor_x, layout)

        if item is self.hovered_item:
            return

        self.hovered_item = item
        self.cancel()
        self._tooltip.update(item, layout)

        if self._preview and self._config.previews_enabled:
            # If hovering a different running item, start timer to show preview
            if item and item.is_running and item.instance_count > 0:
                self._preview_timer_id = GLib.timeout_add(
                    PREVIEW_SHOW_DELAY_MS, self._show_preview, item, layout
                )
            else:
                self._preview.schedule_hide()

    def cancel(self) -> None:
        """Cancel the pending preview timer."""
        if self._preview_timer_id:
            GLib.source_remove(self._preview_timer_id)
            self._preview_timer_id = 0

    def start_anim_pump(self, duration_ms: int = 700) -> None:
        """Start a temporary redraw loop for time-based animations.

        The dock does NOT have a continuous render loop. In normal
        operation, GTK only calls the draw handler when something
        changes (mouse move, model update, etc.). This is efficient —
        a static dock uses zero CPU for rendering.

        However, time-based animations (click darken, launch bounce,
        urgent bounce) need continuous redraws even when the mouse is
        still. Without a pump, the animation would only advance when
        the user happens to move the mouse (triggering motion events).

        The pump is a GLib.timeout_add timer at ~16ms intervals (~60fps)
        that calls queue_draw() for a fixed duration, then self-stops.
        This avoids a permanent render loop — the pump only runs during
        the animation window (e.g., 700ms for a launch bounce).

        If a new animation starts while a pump is already running, the
        old timer is cancelled and replaced. This prevents overlapping
        timers from accumulating.
        """
        if self._anim_timer_id:
            GLib.source_remove(self._anim_timer_id)

        frames_left = [duration_ms // 16]

        def tick() -> bool:
            frames_left[0] -= 1
            if frames_left[0] <= 0:
                self._anim_timer_id = 0
                return False
            self._window.drawing_area.queue_draw()
            return True

        self._anim_timer_id = GLib.timeout_add(16, tick)

    def on_model_changed(self) -> None:
        """Check if any item became urgent and start animation pump."""
        for item in self._model.visible_items():
            if item.is_urgent and item.last_urgent > 0:
                self.start_anim_pump(700)
                break

    def _show_preview(self, item: DockItem, _layout: object) -> bool:
        """Show the preview popup above the hovered icon."""
        self._preview_timer_id = 0
        if not self._preview or self.hovered_item is not item:
            return False

        # Find the layout entry for this item to get screen coordinates
        items = self._model.visible_items()
        layout = compute_layout(
            items,
            self._config,
            self._window.local_cursor_x(),
            item_padding=self._theme.item_padding,
            h_padding=self._theme.h_padding,
        )

        idx = None
        for i, it in enumerate(items):
            if it is item:
                idx = i
                break
        if idx is None or idx >= len(layout):
            return False

        li = layout[idx]
        icon_w = li.scale * self._config.icon_size

        # Convert icon position to absolute screen coordinates
        win_x, win_y = self._window.get_position()
        # Guard: skip if window hasn't been positioned yet
        if win_x == 0 and win_y == 0:
            return False
        icon_abs_x = win_x + li.x + self._window.zoomed_x_offset(layout)

        # Compute the icon's top edge in screen coordinates, not the
        # window top (which includes bounce headroom above the icons)
        _, win_height = self._window.get_size()
        screen_bottom = win_y + win_height
        scaled_size = li.scale * self._config.icon_size
        icon_top_y = screen_bottom - self._theme.bottom_padding - scaled_size

        self._preview.show_for_item(item.desktop_id, icon_abs_x, icon_w, icon_top_y)
        return False
