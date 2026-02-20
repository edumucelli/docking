"""Auto-hide controller — state machine with cubic easing animation."""

from __future__ import annotations

import enum
import math
from typing import TYPE_CHECKING

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib  # noqa: E402

if TYPE_CHECKING:
    from docking.dock_window import DockWindow
    from docking.config import Config

FRAME_INTERVAL_MS = 16  # ~60fps


class HideState(enum.Enum):
    VISIBLE = "visible"
    HIDING = "hiding"
    HIDDEN = "hidden"
    SHOWING = "showing"


def ease_in_cubic(t: float) -> float:
    """Cubic ease-in: slow start, accelerating."""
    return t * t * t


def ease_out_cubic(t: float) -> float:
    """Cubic ease-out: fast start, decelerating."""
    return 1.0 - (1.0 - t) ** 3


class AutoHideController:
    """Manages dock hide/show animation with configurable delays."""

    def __init__(self, window: DockWindow, config: Config) -> None:
        self._window = window
        self._config = config
        self.state = HideState.VISIBLE
        self.hide_offset: float = 0.0  # 0.0 = fully visible, 1.0 = fully hidden

        self._hide_timer_id: int = 0
        self._unhide_timer_id: int = 0
        self._anim_timer_id: int = 0
        self._anim_progress: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._config.autohide

    def on_mouse_leave(self) -> None:
        """Called when mouse leaves the dock area."""
        if not self.enabled:
            return

        self._cancel_unhide_timer()

        if self.state in (HideState.VISIBLE, HideState.SHOWING):
            delay = self._config.hide_delay_ms
            if delay <= 0:
                self._start_hiding()
            else:
                self._hide_timer_id = GLib.timeout_add(delay, self._start_hiding)

    def on_mouse_enter(self) -> None:
        """Called when mouse enters the dock area."""
        if not self.enabled:
            return

        self._cancel_hide_timer()

        if self.state in (HideState.HIDDEN, HideState.HIDING):
            delay = self._config.unhide_delay_ms
            if delay <= 0:
                self._start_showing()
            else:
                self._unhide_timer_id = GLib.timeout_add(delay, self._start_showing)

    def _start_hiding(self) -> bool:
        """Begin hide animation."""
        self._hide_timer_id = 0
        self.state = HideState.HIDING
        self._anim_progress = 0.0
        self._start_animation()
        return GLib.SOURCE_REMOVE

    def _start_showing(self) -> bool:
        """Begin show animation."""
        self._unhide_timer_id = 0
        self.state = HideState.SHOWING
        self._anim_progress = 0.0
        self._start_animation()
        return GLib.SOURCE_REMOVE

    def _start_animation(self) -> None:
        """Start the animation tick loop."""
        if self._anim_timer_id:
            GLib.source_remove(self._anim_timer_id)
        self._anim_timer_id = GLib.timeout_add(FRAME_INTERVAL_MS, self._animation_tick)

    def _animation_tick(self) -> bool:
        """Single animation frame."""
        duration = self._config.hide_time_ms
        step = FRAME_INTERVAL_MS / duration if duration > 0 else 1.0
        self._anim_progress = min(1.0, self._anim_progress + step)

        if self.state == HideState.HIDING:
            self.hide_offset = ease_in_cubic(self._anim_progress)
            if self._anim_progress >= 1.0:
                self.state = HideState.HIDDEN
                self.hide_offset = 1.0
                self._anim_timer_id = 0
                self._window.queue_redraw()
                return GLib.SOURCE_REMOVE

        elif self.state == HideState.SHOWING:
            self.hide_offset = 1.0 - ease_out_cubic(self._anim_progress)
            if self._anim_progress >= 1.0:
                self.state = HideState.VISIBLE
                self.hide_offset = 0.0
                self._anim_timer_id = 0
                self._window.queue_redraw()
                return GLib.SOURCE_REMOVE

        self._window.queue_redraw()
        return GLib.SOURCE_CONTINUE

    def _cancel_hide_timer(self) -> None:
        if self._hide_timer_id:
            GLib.source_remove(self._hide_timer_id)
            self._hide_timer_id = 0

    def _cancel_unhide_timer(self) -> None:
        if self._unhide_timer_id:
            GLib.source_remove(self._unhide_timer_id)
            self._unhide_timer_id = 0
