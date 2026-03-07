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
MIN_HIDE_GRACE_MS = 60


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


def inverse_ease_in_cubic(value: float) -> float:
    """Return t such that ease_in_cubic(t) ~= value."""
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return value ** (1.0 / 3.0)


def inverse_ease_out_cubic(value: float) -> float:
    """Return t such that ease_out_cubic(t) ~= value."""
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return 1.0 - (1.0 - value) ** (1.0 / 3.0)


def _source_exists(source_id: int) -> bool:
    """Return True when a GLib source id is still active."""
    if source_id <= 0:
        return False
    try:
        ctx = GLib.MainContext.default()
        return bool(ctx and ctx.find_source_by_id(source_id))
    except Exception as exc:
        log.debug(f"Could not query GLib source id {source_id}: {exc}")
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
        self._hovered: bool = False
        self._disabled: bool = False

        self._hide_timer_id: int = 0
        self._unhide_timer_id: int = 0
        self._anim_timer_id: int = 0
        self._anim_progress: float = 0.0
        self._hide_after_show: bool = False

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
        self._hovered = False
        self._disabled = False
        self._hide_after_show = False
        self._window.queue_redraw()

    def on_mouse_leave(self) -> None:
        """Called when mouse leaves the dock area."""
        self.set_hovered(hovered=False)

    def on_mouse_enter(self) -> None:
        """Called when mouse enters the dock area."""
        self.set_hovered(hovered=True)

    def set_hovered(self, hovered: bool) -> None:
        """Update dock hover state and reconcile hide/show policy."""
        if not self.enabled:
            return
        if self._hovered == hovered:
            return
        self._hovered = hovered
        log.debug(
            "set_hovered: hovered=%s disabled=%s state=%s",
            hovered,
            self._disabled,
            self.state.value,
        )
        self._update_hidden()

    def set_disabled(self, disabled: bool, *, reason: str = "unknown") -> None:
        """Disable or enable autohide reactions while menus/drags are active."""
        if not self.enabled:
            return
        if self._disabled == disabled:
            return
        self._disabled = disabled
        log.debug(
            "set_disabled: hovered=%s disabled=%s state=%s reason=%s",
            self._hovered,
            disabled,
            self.state.value,
            reason,
        )
        self._update_hidden()

    def _update_hidden(self) -> None:
        """Reconcile hover/disabled state into show-or-hide behavior."""
        if self._disabled or self._hovered:
            self._show()
        else:
            self._hide()

    def _show(self) -> None:
        self.zoom_progress = 1.0
        self._cancel_hide_timer()
        self._hide_after_show = False
        if self.state in (HideState.HIDDEN, HideState.HIDING):
            delay = self._config.unhide_delay_ms
            if delay <= 0:
                self._start_showing()
            else:
                self._unhide_timer_id = GLib.timeout_add(delay, self._start_showing)

    def _hide(self) -> None:
        self._cancel_unhide_timer()

        if self.state == HideState.SHOWING:
            self._hide_after_show = True
            return

        if self.state == HideState.VISIBLE:
            self._schedule_hide()

    def _start_hiding(self) -> bool:
        """Begin hide animation."""
        self._hide_timer_id = 0
        self._hide_after_show = False
        self.state = HideState.HIDING
        if self.hide_offset > 0.0:
            self._anim_progress = inverse_ease_in_cubic(self.hide_offset)
        else:
            self._anim_progress = 0.0
        self._start_animation()
        return False

    def _start_showing(self) -> bool:
        """Begin show animation."""
        self._unhide_timer_id = 0
        self.state = HideState.SHOWING
        if self.hide_offset > 0.0:
            self._anim_progress = inverse_ease_out_cubic(1.0 - self.hide_offset)
        else:
            self._anim_progress = 0.0
        self._start_animation()
        return False

    def _schedule_hide(self) -> None:
        delay = self._config.hide_delay_ms
        if delay <= 0 and self.state == HideState.VISIBLE:
            delay = MIN_HIDE_GRACE_MS

        if delay <= 0:
            self._start_hiding()
        else:
            self._hide_timer_id = GLib.timeout_add(delay, self._start_hiding)

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
                if self._hide_after_show:
                    self._hide_after_show = False
                    self._schedule_hide()
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
