"""Hover manager -- icon hover detection, preview popup timer, animation pump."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib  # noqa: E402

from docking.core.position import Position
from docking.core.zoom import compute_layout

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.theme import Theme
    from docking.platform.model import DockModel, DockItem
    from docking.ui.preview import PreviewPopup
    from docking.ui.tooltip import TooltipManager
    from docking.ui.dock_window import DockWindow

PREVIEW_SHOW_DELAY_MS = 400


class HoverManager:
    """Tracks which dock icon is hovered and manages preview/animation timers."""

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
        self._anim_timer_id: int = 0

    def set_preview(self, preview: PreviewPopup) -> None:
        self._preview = preview

    def update(self, cursor_main: float) -> None:
        """Detect which item the cursor is over and manage preview timer."""
        items = self._model.visible_items()
        layout = compute_layout(
            items,
            self._config,
            self._window.local_cursor_main(),
            item_padding=self._theme.item_padding,
            h_padding=self._theme.h_padding,
        )
        item = self._window.hit_test(cursor_main, layout)

        if item is self.hovered_item:
            return

        self.hovered_item = item
        self.cancel()
        self._tooltip.update(item, layout)

        if self._preview and self._config.previews_enabled:
            if item and item.is_running and item.instance_count > 0:
                self._preview_timer_id = GLib.timeout_add(
                    PREVIEW_SHOW_DELAY_MS, self._show_preview, item, layout
                )
            else:
                self._preview.schedule_hide()

    def cancel(self) -> None:
        if self._preview_timer_id:
            GLib.source_remove(self._preview_timer_id)
            self._preview_timer_id = 0

    def start_anim_pump(self, duration_ms: int = 700) -> None:
        """Start a temporary redraw loop for time-based animations."""
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
        for item in self._model.visible_items():
            if item.is_urgent and item.last_urgent > 0:
                self.start_anim_pump(700)
                break

    def _show_preview(self, item: DockItem, _layout: object) -> bool:
        """Show the preview popup near the hovered icon."""
        self._preview_timer_id = 0
        if not self._preview or self.hovered_item is not item:
            return False

        items = self._model.visible_items()
        layout = compute_layout(
            items,
            self._config,
            self._window.local_cursor_main(),
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
        pos = self._config.pos

        win_x, win_y = self._window.get_position()
        if win_x == 0 and win_y == 0:
            return False

        main_offset = self._window.zoomed_main_offset(layout)
        win_w, win_h = self._window.get_size()
        edge_padding = self._theme.bottom_padding
        scaled_size = li.scale * self._config.icon_size

        if pos == Position.BOTTOM:
            icon_abs_x = win_x + li.x + main_offset
            icon_top_y = win_y + win_h - edge_padding - scaled_size
            self._preview.show_for_item(
                item.desktop_id, icon_abs_x, icon_w, icon_top_y, pos
            )
        elif pos == Position.TOP:
            icon_abs_x = win_x + li.x + main_offset
            icon_bottom_y = win_y + edge_padding + scaled_size
            self._preview.show_for_item(
                item.desktop_id, icon_abs_x, icon_w, icon_bottom_y, pos
            )
        elif pos == Position.LEFT:
            icon_abs_y = win_y + li.x + main_offset
            icon_right_x = win_x + edge_padding + scaled_size
            self._preview.show_for_item(
                item.desktop_id, icon_right_x, icon_w, icon_abs_y, pos
            )
        else:  # RIGHT
            icon_abs_y = win_y + li.x + main_offset
            icon_left_x = win_x + win_w - edge_padding - scaled_size
            self._preview.show_for_item(
                item.desktop_id, icon_left_x, icon_w, icon_abs_y, pos
            )
        return False
