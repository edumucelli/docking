"""Hover state machine and preview timing orchestration.

HoverManager sits between raw pointer movement and user-facing hover effects.
Its job is to keep hover-dependent UI behavior synchronized: tooltips, preview
show/hide timers, and short redraw pumps for time-based animations.

What problem this module solves

Several features depend on "current hovered item," but they react on different
timescales:

- tooltip text should update immediately,
- preview popup should appear only after a delay,
- preview should hide when hover changes,
- click/urgent animations require bounded frame pumping.

If each subsystem ran independent hover/timer logic, they would drift and race
(for example, tooltip over one icon while preview opens for another). This
manager centralizes the state transitions.

Hover and preview lifecycle

The behavior is:

1. pointer moves -> hit-test current item from latest layout,
2. tooltip is updated on every move,
3. if hovered item changed, cancel pending preview show,
4. if new item is previewable (running app with windows), arm delayed show,
5. on timer fire, recompute layout and position preview from current geometry.

That final recomputation is critical: layout captured when timer was created may
be stale due to cursor movement, zoom animation, autohide movement, or model
changes.

Animation pump responsibility

Some visual effects are short and time-based (click darken, urgent bounce).
HoverManager provides one shared frame pump (~60 fps for bounded duration)
instead of many ad-hoc timers. This keeps redraw behavior predictable and
reduces timer proliferation across the UI layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib  # noqa: E402

from docking.core.position import Position
from docking.log import get_logger
from docking.ui.geometry import DockGeometryFrame

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
        frame = frame or self._window._get_geometry_frame()
        item = (
            frame.item_at_point(self._window.cursor_x, self._window.cursor_y)
            if cursor_main >= 0
            else None
        )

        # Always refresh tooltip (item.name may change while hovered)
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

    def start_anim_pump(self, duration_ms: int = 700) -> None:
        """Start a temporary 60fps redraw loop for time-based animations.

        Used for click darken (300ms), launch bounce (600ms), and urgent
        bounce. The pump self-terminates after duration_ms.
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
        """Start anim pump if any item became urgent (needs bounce animation)."""
        for item in self._model.visible_items():
            if item.is_urgent and item.last_urgent > 0:
                self.start_anim_pump(duration_ms=700)
                break

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

        frame = self._window._get_geometry_frame()
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
