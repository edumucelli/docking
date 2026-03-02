"""Auto-hide controller -- state machine with cubic easing animation."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from docking.log import get_logger

log = get_logger(name="autohide")

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib  # noqa: E402

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.ui.dock_window import DockWindow

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


def _source_exists(source_id: int) -> bool:
    """Return True when a GLib source id is still active."""
    if source_id <= 0:
        return False
    try:
        ctx = GLib.MainContext.default()
        return bool(ctx and ctx.find_source_by_id(source_id))
    except Exception as exc:
        log.debug("Could not query GLib source id %s: %s", source_id, exc)
        # If runtime doesn't expose the check, fall back to best effort.
        return True


def _clear_source(source_id: int) -> int:
    """Safely remove a GLib source if it still exists and return zero id."""
    if _source_exists(source_id=source_id):
        GLib.source_remove(source_id)
    return 0


class AutoHideController:
    """Manages dock hide/show animation with configurable delays."""

    def __init__(self, window: DockWindow, config: Config) -> None:
        self._window = window
        self._config = config
        self.state = HideState.VISIBLE
        self.hide_offset: float = 0.0  # 0.0 = fully visible, 1.0 = fully hidden
        self.zoom_progress: float = 0.0  # 0.0 = no zoom, 1.0 = full zoom

        self._hide_timer_id: int = 0
        self._unhide_timer_id: int = 0
        self._anim_timer_id: int = 0
        self._anim_progress: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._config.autohide

    def reset(self) -> None:
        """Force dock visible -- call when auto-hide is toggled off."""
        self._cancel_hide_timer()
        self._cancel_unhide_timer()
        if self._anim_timer_id:
            self._anim_timer_id = _clear_source(source_id=self._anim_timer_id)
        self.state = HideState.VISIBLE
        self.hide_offset = 0.0
        self.zoom_progress = 0.0
        self._window.queue_redraw()

    def on_mouse_leave(self) -> None:
        """Called when mouse leaves the dock area."""
        if not self.enabled:
            return
        log.debug("on_mouse_leave: state=%s", self.state.value)

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
        log.debug("on_mouse_enter: state=%s", self.state.value)

        self.zoom_progress = 1.0
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
        return False

    def _start_showing(self) -> bool:
        """Begin show animation."""
        self._unhide_timer_id = 0
        self.state = HideState.SHOWING
        self._anim_progress = 0.0
        self._start_animation()
        return False

    def _start_animation(self) -> None:
        """Start the animation tick loop."""
        if self._anim_timer_id:
            self._anim_timer_id = _clear_source(source_id=self._anim_timer_id)
        self._anim_timer_id = GLib.timeout_add(FRAME_INTERVAL_MS, self._animation_tick)

    # Autohide state machine:
    #
    #   ┌─────────┐  mouse   ┌────────┐  anim    ┌────────┐
    #   │ VISIBLE │──leave──->│ HIDING │──done───->│ HIDDEN │
    #   └─────────┘          └────────┘          └────────┘
    #       ^                                        │
    #       │                ┌─────────┐   mouse     │
    #       └───anim done────│ SHOWING │<-──enter─────┘
    #                        └─────────┘
    #
    # HIDING:  hide_offset animates 0->1 using ease_in_cubic (accelerating)
    #          zoom_progress decays in parallel
    # SHOWING: hide_offset animates 1->0 using ease_out_cubic (decelerating)
    # VISIBLE/HIDDEN: stable states, no animation running
    #
    # Each animation frame advances _anim_progress by a fixed step
    # (FRAME_INTERVAL_MS / hide_time_ms), giving consistent timing
    # regardless of how many frames actually render.

    def _animation_tick(self) -> bool:
        """Single animation frame."""
        duration = self._config.hide_time_ms
        step = FRAME_INTERVAL_MS / duration if duration > 0 else 1.0
        self._anim_progress = min(1.0, self._anim_progress + step)

        if self.state == HideState.HIDING:
            self.hide_offset = ease_in_cubic(t=self._anim_progress)
            # Zoom progress decay -- smooth zoom fadeout during hide.
            #
            # As the dock slides down (hide_offset goes 0.0 -> 1.0), we
            # simultaneously decay the zoom effect. The formula:
            #   zoom_progress *= (1.0 - hide_offset)
            #
            # This is a multiplicative decay that couples zoom to the
            # hide animation. Early in the hide (hide_offset ≈ 0.1),
            # zoom_progress drops by ~10%. Late in the hide
            # (hide_offset ≈ 0.9), it drops rapidly toward zero.
            #
            # The visual effect: icons gradually shrink back to their
            # rest size AS the dock slides away, rather than snapping
            # to unzoomed before the slide starts.
            # Plank's formula: direct linear decay, not compounding.
            # zoom_in_progress = zoom_progress * (1 - hide_progress)
            # We keep zoom_progress at its initial value (set to 1.0 on
            # mouse_enter) and let the renderer apply the decay.
            self.zoom_progress = 1.0 - self.hide_offset
            if self._anim_progress >= 1.0:
                self.state = HideState.HIDDEN
                self.hide_offset = 1.0
                self.zoom_progress = 0.0
                self._anim_timer_id = 0
                self._window.queue_redraw()
                return False

        elif self.state == HideState.SHOWING:
            self.hide_offset = 1.0 - ease_out_cubic(t=self._anim_progress)
            self.zoom_progress = 1.0 - self.hide_offset
            if self._anim_progress >= 1.0:
                self.state = HideState.VISIBLE
                self.hide_offset = 0.0
                self._anim_timer_id = 0
                self._window.queue_redraw()
                return False

        self._window.queue_redraw()
        return True

    def _cancel_hide_timer(self) -> None:
        if self._hide_timer_id:
            self._hide_timer_id = _clear_source(source_id=self._hide_timer_id)

    def _cancel_unhide_timer(self) -> None:
        if self._unhide_timer_id:
            self._unhide_timer_id = _clear_source(source_id=self._unhide_timer_id)
